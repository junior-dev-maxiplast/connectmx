from django import forms
from django.db.models import Q
from accounts.models import User
from .models import (
    userQueue,
    TaskGroup,
    TaskType,
    UserQueueFieldOption,
    ChecklistTemplate,
    ChecklistSection,
    ChecklistField,
    ChecklistChoiceGroup,
    ChecklistChoiceOption,
    Project,
    ProjectRoadmapItem,
    ProjectKanbanColumn,
    ProjectKanbanCard,
    HubToolCategory,
    HubTool,
    HubUserTool,
    HubUserToolCategory,
    KnowledgeCategory,
    KnowledgeEntry,
    DemandTemplate,
    DemandTemplateDetail,
    MaintenanceType,
    MaintenanceSituation,
    MaintenanceIndicator,
    MaintenanceSystemGroup,
    MaintenanceSystem,
    MaintenanceEvent,
    MyAgendaReminder,
    PortalDemand,
    PortalDemandSlaPolicy,
    PortalCannedResponse,
    PortalDemandCustomField,
    PortalDemandCustomFieldOption,
)
from decimal import Decimal


class TaskTypeSelect(forms.Select):
    """
    Adds data-group-id to each TaskType option so the frontend can filter by selected TaskGroup.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)

        # For ModelChoiceFields Django uses ModelChoiceIteratorValue (has .instance).
        instance = getattr(value, "instance", None)

        if instance is not None and isinstance(instance, TaskType):
            option["attrs"]["data-group-id"] = str(instance.group_id)

        return option


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(entry, initial) for entry in data]
        return single_file_clean(data, initial)

class UserQueueCreateForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        collaborator_queryset = User.objects.exclude(pk=getattr(user, "pk", None)).order_by('nameUser', 'username')
        self.fields['task_group'].queryset = TaskGroup.objects.all().order_by('name')
        self.fields['task_type'].queryset = TaskType.objects.select_related('group').order_by('group__name', 'name')
        self.fields['extra_collaborators'].queryset = collaborator_queryset
        self.fields['task_group'].required = False
        self.fields['task_type'].required = False
        self.fields['extra_collaborators'].required = False

        self.fields['task_group'].widget.attrs.update({'class': 'queue-select'})
        self.fields['task_type'].widget = TaskTypeSelect(attrs={'class': 'queue-select'})
        self.fields['task_type'].widget.choices = self.fields['task_type'].choices
        self.fields['extra_collaborators'].widget = forms.SelectMultiple(
            attrs={'class': 'queue-collaborator-select', 'style': 'display:none;'}
        )

        priority_choices = self._field_option_choices(
            user,
            UserQueueFieldOption.FIELD_PRIORITY,
            userQueue.default_field_options(userQueue.FIELD_PRIORITY),
        )
        effort_choices = self._field_option_choices(
            user,
            UserQueueFieldOption.FIELD_EFFORT,
            userQueue.default_field_options(userQueue.FIELD_EFFORT),
        )
        self.fields['priority_level'] = forms.ChoiceField(
            choices=priority_choices,
            required=False,
            label='Prioridade',
            widget=forms.Select(attrs={'class': 'queue-select'}),
        )
        self.fields['estimated_effort_level'] = forms.ChoiceField(
            choices=effort_choices,
            required=False,
            label='Nivel estimado',
            widget=forms.Select(attrs={'class': 'queue-select'}),
        )

    @staticmethod
    def _field_option_choices(user, field_key, defaults):
        if user is not None:
            rows = list(
                UserQueueFieldOption.objects.filter(user=user, field_key=field_key, is_active=True).order_by('sort_order', 'id')
            )
            if rows:
                return [(row.value, row.label) for row in rows]
        return [(value, label) for value, label, _ in defaults]

    class Meta:
        model = userQueue
        fields = [
            'a_ticket',
            'f_conclusion_rate',
            'a_description',
            'task_group',
            'task_type',
            'extra_collaborators',
            'priority_level',
            'estimated_effort_level',
            'd_real_date_start',
            'd_real_time_start',
            'd_real_date_end',
            't_real_time_end',
        ]

        labels = {
            'user_code': 'Código de Usuário',
            'n_register': 'Registro Único',
            'a_ticket': 'Chamado',
            'f_conclusion_rate': '% Conclusão',
            'n_status_code':'Status',
            'a_description':'Descrição',
            'task_group': 'Grupo',
            'task_type': 'Tipo',
            'extra_collaborators': 'Colaboradores extras',
            'n_type_group':'Tipo - Grupo',
            'n_type_code':'Tipo - Código',
            'd_predicted_date_start':'Início Previsto - Data',
            'd_predicted_date_end':'Fim Previsto - Data',
            't_predicted_time_start':'Início Previsto - Hora',
            't_predicted_time_end':'Fim Previsto - Hora',
            'f_total_predicted_time':'Tempo total Previsto',
            'd_real_date_start':'Início Real - Data',
            'd_real_date_end':'Fim Real - Data',
            'd_real_time_start':'Início Real - Hora',
            't_real_time_end':'Fim Real - Hora',
            'f_total_real_time':'Tempo Total Realizado',
            'f_predicted_real_diference':'Diferença Previsto x Realizado',
            'n_queue_position':'Posição na Fila',

        }

        widgets = {
            'd_real_date_start':forms.DateInput(attrs={'type':'date'}),
            'd_real_date_end':forms.DateInput(attrs={'type':'date'}),
            'd_real_time_start':forms.TimeInput(attrs={'type':'time'}),
            't_real_time_end':forms.TimeInput(attrs={'type':'time'}),
            'n_queue_position':forms.HiddenInput()
        }


class UserQueueUpdateForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        priority_choices = UserQueueCreateForm._field_option_choices(
            user,
            UserQueueFieldOption.FIELD_PRIORITY,
            userQueue.default_field_options(userQueue.FIELD_PRIORITY),
        )
        effort_choices = UserQueueCreateForm._field_option_choices(
            user,
            UserQueueFieldOption.FIELD_EFFORT,
            userQueue.default_field_options(userQueue.FIELD_EFFORT),
        )
        self.fields['priority_level'] = forms.ChoiceField(
            choices=priority_choices,
            required=False,
            label='Prioridade',
            widget=forms.Select(attrs={'class': 'queue-select'}),
        )
        self.fields['estimated_effort_level'] = forms.ChoiceField(
            choices=effort_choices,
            required=False,
            label='Nivel estimado',
            widget=forms.Select(attrs={'class': 'queue-select'}),
        )

    class Meta:
        model = userQueue
        exclude = [
            'user_code',
            'n_queue_position',
            'f_total_predicted_time',
            'f_total_real_time',
            'f_predicted_real_diference',
            'extra_collaborators',
            'task_group',
            'task_type',
            'linked_project',
            'linked_roadmap_item',
        ]

        widgets = {
            'd_predicted_date_start': forms.DateInput(attrs={'type': 'date'}),
            'd_predicted_date_end': forms.DateInput(attrs={'type': 'date'}),
            't_predicted_time_start': forms.TimeInput(attrs={'type': 'time'}),
            't_predicted_time_end': forms.TimeInput(attrs={'type': 'time'}),
            'd_real_date_start': forms.DateInput(attrs={'type': 'date'}),
            'd_real_date_end': forms.DateInput(attrs={'type': 'date'}),
            'd_real_time_start': forms.TimeInput(attrs={'type': 'time'}),
            't_real_time_end': forms.TimeInput(attrs={'type': 'time'}),
        }


class TaskGroupForm(forms.ModelForm):
    class Meta:
        model = TaskGroup
        fields = ['name']

        labels = {
            'name': 'Grupo',
        }


class TaskTypeForm(forms.ModelForm):
    class Meta:
        model = TaskType
        fields = ['group', 'name', 'color']

        labels = {
            'group': 'Grupo',
            'name': 'Tipo',
            'color': 'Cor',
        }

        widgets = {
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'queue-color'}),
        }


class ChecklistTemplateForm(forms.ModelForm):
    class Meta:
        model = ChecklistTemplate
        fields = ["name", "description", "is_active"]

        labels = {
            "name": "Modelo",
            "description": "Descricao",
            "is_active": "Ativo",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class ChecklistSectionForm(forms.ModelForm):
    class Meta:
        model = ChecklistSection
        fields = ["template", "title", "sort_order"]

        labels = {
            "template": "Modelo",
            "title": "Secao",
            "sort_order": "Ordem",
        }


class ChecklistFieldForm(forms.ModelForm):
    class Meta:
        model = ChecklistField
        fields = [
            "section",
            "label",
            "help_text",
            "field_type",
            "required",
            "sort_order",
            "choice_group",
        ]

        labels = {
            "section": "Secao",
            "label": "Campo",
            "help_text": "Ajuda",
            "field_type": "Tipo",
            "required": "Obrigatorio",
            "sort_order": "Ordem",
            "choice_group": "Opcoes",
        }

        widgets = {
            "help_text": forms.TextInput(attrs={"placeholder": "Opcional"}),
        }


class ChecklistChoiceGroupForm(forms.ModelForm):
    class Meta:
        model = ChecklistChoiceGroup
        fields = ["name", "description"]

        labels = {
            "name": "Grupo",
            "description": "Descricao",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class ChecklistChoiceOptionForm(forms.ModelForm):
    class Meta:
        model = ChecklistChoiceOption
        fields = ["group", "label"]

        labels = {
            "group": "Grupo",
            "label": "Opcao",
        }


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "developer", "participants", "status", "color", "start_date", "end_date"]

        labels = {
            "name": "Projeto",
            "description": "Descricao",
            "developer": "Responsável",
            "participants": "Participantes",
            "status": "Status",
            "color": "Cor",
            "start_date": "Inicio",
            "end_date": "Fim",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "color": forms.TextInput(attrs={"type": "color", "class": "queue-color"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["developer"].queryset = User.objects.order_by("nameUser", "username")
        self.fields["participants"].queryset = User.objects.order_by("nameUser", "username")
        self.fields["participants"].widget = forms.SelectMultiple(attrs={"size": 5})


class ProjectRoadmapItemForm(forms.ModelForm):
    class Meta:
        model = ProjectRoadmapItem
        fields = ["project", "responsible", "title", "description", "status", "start_date", "end_date", "sort_order"]

        labels = {
            "project": "Projeto",
            "responsible": "Responsavel",
            "title": "Etapa",
            "description": "Descricao",
            "status": "Status",
            "start_date": "Inicio",
            "end_date": "Fim",
            "sort_order": "Ordem",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsible"].queryset = User.objects.order_by("nameUser", "username")


class DemandTemplateForm(forms.ModelForm):
    class Meta:
        model = DemandTemplate
        fields = [
            "name",
            "description",
            "task_group",
            "task_type",
            "linked_project",
            "predicted_start_offset_hours",
            "predicted_end_offset_hours",
            "is_active",
        ]
        labels = {
            "name": "Nome do modelo",
            "description": "Descrição",
            "task_group": "Grupo",
            "task_type": "Tipo",
            "linked_project": "Projeto vinculado",
            "predicted_start_offset_hours": "Offset início (horas)",
            "predicted_end_offset_hours": "Offset fim (horas)",
            "is_active": "Ativo",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class DemandTemplateDetailForm(forms.ModelForm):
    class Meta:
        model = DemandTemplateDetail
        fields = ["template", "description", "sort_order"]
        labels = {
            "template": "Modelo",
            "description": "Subtarefa padrão",
            "sort_order": "Ordem",
        }


class ProjectKanbanColumnForm(forms.ModelForm):
    class Meta:
        model = ProjectKanbanColumn
        fields = ["project", "name", "color", "sort_order"]

        labels = {
            "project": "Projeto",
            "name": "Coluna",
            "color": "Cor",
            "sort_order": "Ordem",
        }

        widgets = {
            "color": forms.TextInput(attrs={"type": "color", "class": "queue-color"}),
        }


class ProjectKanbanCardForm(forms.ModelForm):
    class Meta:
        model = ProjectKanbanCard
        fields = ["project", "column", "title", "description", "priority", "due_date", "sort_order"]

        labels = {
            "project": "Projeto",
            "column": "Coluna",
            "title": "Card",
            "description": "Descricao",
            "priority": "Prioridade",
            "due_date": "Entrega",
            "sort_order": "Ordem",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class HubToolCategoryForm(forms.ModelForm):
    class Meta:
        model = HubToolCategory
        fields = ["name", "sort_order", "is_active"]

        labels = {
            "name": "Categoria",
            "sort_order": "Ordem",
            "is_active": "Ativa",
        }


class HubToolForm(forms.ModelForm):
    class Meta:
        model = HubTool
        fields = ["category", "name", "link", "image_url", "sort_order", "is_active"]

        labels = {
            "category": "Categoria",
            "name": "Ferramenta",
            "link": "Link",
            "image_url": "Imagem (URL)",
            "sort_order": "Ordem",
            "is_active": "Ativa",
        }


class HubUserToolForm(forms.ModelForm):
    class Meta:
        model = HubUserTool
        fields = ["category", "name", "link", "image_url", "sort_order", "is_active"]

        labels = {
            "category": "Categoria",
            "name": "Ferramenta",
            "link": "Link",
            "image_url": "Imagem (URL)",
            "sort_order": "Ordem",
            "is_active": "Ativa",
        }


class HubUserToolCategoryForm(forms.ModelForm):
    class Meta:
        model = HubUserToolCategory
        fields = ["name", "sort_order", "is_active"]

        labels = {
            "name": "Categoria",
            "sort_order": "Ordem",
            "is_active": "Ativa",
        }


class KnowledgeCategoryForm(forms.ModelForm):
    class Meta:
        model = KnowledgeCategory
        fields = ["name", "description", "sort_order", "is_active"]

        labels = {
            "name": "Categoria",
            "description": "Descricao",
            "sort_order": "Ordem",
            "is_active": "Ativa",
        }

        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class KnowledgeEntryForm(forms.ModelForm):
    class Meta:
        model = KnowledgeEntry
        fields = [
            "category",
            "title",
            "trigger",
            "description",
            "impact",
            "workaround",
            "root_cause",
            "resolution",
            "tags",
            "is_resolved",
        ]

        labels = {
            "category": "Categoria",
            "title": "Titulo",
            "trigger": "Situacao / problema",
            "description": "Descricao detalhada",
            "impact": "Impacto",
            "workaround": "Contorno aplicado",
            "root_cause": "Causa raiz",
            "resolution": "Solucao definitiva",
            "tags": "Tags (separadas por virgula)",
            "is_resolved": "Resolvido",
        }

        widgets = {
            "trigger": forms.Textarea(attrs={"rows": 3, "class": "kb-autogrow"}),
            "description": forms.Textarea(attrs={"rows": 4, "class": "kb-autogrow"}),
            "impact": forms.Textarea(attrs={"rows": 2, "class": "kb-autogrow"}),
            "workaround": forms.Textarea(attrs={"rows": 2, "class": "kb-autogrow"}),
            "root_cause": forms.Textarea(attrs={"rows": 2, "class": "kb-autogrow"}),
            "resolution": forms.Textarea(attrs={"rows": 3, "class": "kb-autogrow"}),
        }


class MaintenanceTypeForm(forms.ModelForm):
    class Meta:
        model = MaintenanceType
        fields = ["name", "color", "is_active"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color", "class": "queue-color"}),
        }


class MaintenanceSituationForm(forms.ModelForm):
    class Meta:
        model = MaintenanceSituation
        fields = ["name", "is_active"]


class MaintenanceIndicatorForm(forms.ModelForm):
    class Meta:
        model = MaintenanceIndicator
        fields = ["name", "is_incident", "is_active"]


class MaintenanceSystemGroupForm(forms.ModelForm):
    class Meta:
        model = MaintenanceSystemGroup
        fields = ["name", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class MaintenanceSystemForm(forms.ModelForm):
    class Meta:
        model = MaintenanceSystem
        fields = ["group", "name", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class MaintenanceEventForm(forms.ModelForm):
    class Meta:
        model = MaintenanceEvent
        fields = [
            "title",
            "short_description",
            "full_description",
            "maintenance_type",
            "situation",
            "indicator",
            "system_group",
            "affected_systems",
            "scheduled_start",
            "expected_return",
            "real_return",
            "is_outage",
        ]
        widgets = {
            "short_description": forms.Textarea(attrs={"rows": 2}),
            "full_description": forms.Textarea(attrs={"rows": 4, "class": "kb-autogrow"}),
            "scheduled_start": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "expected_return": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "real_return": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["affected_systems"].queryset = MaintenanceSystem.objects.filter(is_active=True).order_by("name")
        self.fields["affected_systems"].widget = forms.CheckboxSelectMultiple()


class MyAgendaReminderForm(forms.ModelForm):
    class Meta:
        model = MyAgendaReminder
        fields = ["title", "description", "color", "reminder_date", "reminder_time", "priority", "is_done"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "color": forms.TextInput(attrs={"type": "color", "class": "queue-color"}),
            "reminder_date": forms.DateInput(attrs={"type": "date"}),
            "reminder_time": forms.TimeInput(attrs={"type": "time"}),
        }


class PortalDemandForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        label="Anexos iniciais",
        widget=MultipleFileInput(attrs={"multiple": True}),
    )
    priority_level = forms.ChoiceField(
        choices=[(value, label) for value, label, _color in userQueue.default_field_options(userQueue.FIELD_PRIORITY)],
        required=False,
        label="Prioridade",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )

    class Meta:
        model = PortalDemand
        fields = ["title", "description", "task_group", "task_type", "priority_level"]
        labels = {
            "title": "Título da demanda",
            "description": "Descreva a necessidade",
            "task_group": "Grupo",
            "task_type": "Tipo",
            "priority_level": "Prioridade",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Ex.: Ajuste no relatório de vendas"}),
            "description": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Explique o contexto, o problema e o resultado esperado.",
                }
            ),
            "task_group": forms.Select(attrs={"class": "queue-select"}),
            "task_type": TaskTypeSelect(attrs={"class": "queue-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.portal_custom_fields = []
        self.fields["task_group"].required = False
        self.fields["task_type"].required = False
        self.fields["task_group"].queryset = TaskGroup.objects.all().order_by("name")
        self.fields["task_type"].queryset = TaskType.objects.select_related("group").order_by("group__name", "name")
        self.fields["task_type"].widget.choices = self.fields["task_type"].choices
        custom_fields = list(
            PortalDemandCustomField.objects.filter(is_active=True)
            .prefetch_related("options", "task_groups", "task_types")
            .order_by("sort_order", "id")
        )
        for definition in custom_fields:
            field_name = f"custom_field_{definition.id}"
            self.fields[field_name] = self._build_custom_field(definition)
            self.portal_custom_fields.append((field_name, definition))

    def _build_custom_field(self, definition):
        common = {
            "required": False,
            "label": definition.label,
            "help_text": definition.help_text or "",
        }
        placeholder = (definition.placeholder or "").strip()
        if definition.field_type == PortalDemandCustomField.FIELD_TEXTAREA:
            return forms.CharField(
                **common,
                widget=forms.Textarea(
                    attrs={
                        "rows": 4,
                        "placeholder": placeholder or f"Informe {definition.label.lower()}",
                    }
                ),
            )
        if definition.field_type == PortalDemandCustomField.FIELD_SELECT:
            choices = [("", "Selecione")]
            choices.extend(
                (option.value, option.label)
                for option in definition.options.all()
                if getattr(option, "is_active", True)
            )
            return forms.ChoiceField(
                **common,
                choices=choices,
                widget=forms.Select(attrs={"class": "queue-select"}),
            )
        if definition.field_type == PortalDemandCustomField.FIELD_NUMBER:
            return forms.DecimalField(
                **common,
                decimal_places=2,
                max_digits=14,
                widget=forms.NumberInput(
                    attrs={
                        "step": "0.01",
                        "placeholder": placeholder or "0,00",
                    }
                ),
            )
        if definition.field_type == PortalDemandCustomField.FIELD_DATE:
            return forms.DateField(
                **common,
                input_formats=["%Y-%m-%d"],
                widget=forms.DateInput(attrs={"type": "date"}),
            )
        if definition.field_type == PortalDemandCustomField.FIELD_CHECKBOX:
            return forms.BooleanField(
                **common,
                widget=forms.CheckboxInput(attrs={"class": "modern-checkbox-input"}),
            )
        return forms.CharField(
            **common,
            widget=forms.TextInput(
                attrs={
                    "placeholder": placeholder or f"Informe {definition.label.lower()}",
                }
            ),
        )

    def get_dynamic_fields(self):
        rows = []
        for field_name, definition in self.portal_custom_fields:
            rows.append(
                {
                    "field": self[field_name],
                    "definition": definition,
                    "is_full_width": definition.field_type == PortalDemandCustomField.FIELD_TEXTAREA,
                    "group_ids": list(definition.task_groups.values_list("id", flat=True)),
                    "type_ids": list(definition.task_types.values_list("id", flat=True)),
                }
            )
        return rows

    def _selected_scope(self, cleaned):
        task_group = cleaned.get("task_group")
        task_type = cleaned.get("task_type")
        group_id = getattr(task_group, "id", None)
        type_id = getattr(task_type, "id", None)
        if not group_id and task_type:
            group_id = getattr(task_type, "group_id", None)
        return group_id, type_id

    @staticmethod
    def _definition_applies_to_scope(definition, group_id, type_id):
        scoped_group_ids = list(definition.task_groups.values_list("id", flat=True))
        scoped_type_ids = list(definition.task_types.values_list("id", flat=True))
        if scoped_type_ids:
            return bool(type_id and type_id in scoped_type_ids)
        if scoped_group_ids:
            return bool(group_id and group_id in scoped_group_ids)
        return True

    def _serialize_custom_value(self, definition, raw_value):
        if definition.field_type == PortalDemandCustomField.FIELD_CHECKBOX:
            return "1" if raw_value else "0"
        if raw_value in (None, ""):
            return ""
        if definition.field_type == PortalDemandCustomField.FIELD_DATE and hasattr(raw_value, "strftime"):
            return raw_value.strftime("%Y-%m-%d")
        if definition.field_type == PortalDemandCustomField.FIELD_NUMBER and isinstance(raw_value, Decimal):
            return format(raw_value, "f")
        return str(raw_value).strip()

    def clean(self):
        cleaned = super().clean()
        task_group = cleaned.get("task_group")
        task_type = cleaned.get("task_type")

        if task_type and task_group and task_type.group_id != task_group.id:
            self.add_error("task_type", "O tipo selecionado não pertence ao grupo informado.")
        elif task_type and not task_group:
            cleaned["task_group"] = task_type.group

        group_id, type_id = self._selected_scope(cleaned)
        for field_name, definition in self.portal_custom_fields:
            if not self._definition_applies_to_scope(definition, group_id, type_id):
                cleaned[field_name] = None
                continue

            raw_value = cleaned.get(field_name)
            if definition.field_type == PortalDemandCustomField.FIELD_CHECKBOX:
                has_value = bool(raw_value)
            else:
                has_value = self._serialize_custom_value(definition, raw_value) not in ("", None)

            if definition.is_required and not has_value:
                self.add_error(field_name, f"Informe {definition.label.lower()}.")

        return cleaned

    def save_custom_values(self, demand):
        group_id = getattr(demand.task_group, "id", None) or getattr(demand.task_type, "group_id", None)
        type_id = getattr(demand.task_type, "id", None)
        for field_name, definition in self.portal_custom_fields:
            if not self._definition_applies_to_scope(definition, group_id, type_id):
                demand.custom_values.filter(field=definition).delete()
                continue
            serialized = self._serialize_custom_value(definition, self.cleaned_data.get(field_name))
            if definition.field_type != PortalDemandCustomField.FIELD_CHECKBOX and not serialized:
                demand.custom_values.filter(field=definition).delete()
                continue
            PortalDemandCustomValue = demand.custom_values.model
            PortalDemandCustomValue.objects.update_or_create(
                demand=demand,
                field=definition,
                defaults={"value": serialized},
            )


class PortalDemandReplyForm(forms.Form):
    message = forms.CharField(
        required=False,
        label="Nova mensagem",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
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
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
            },
            format="%Y-%m-%dT%H:%M",
        ),
    )
    work_ended_at = forms.DateTimeField(
        required=False,
        label="Fim do trabalho",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
            },
            format="%Y-%m-%dT%H:%M",
        ),
    )

    def clean(self):
        cleaned = super().clean()
        message = (cleaned.get("message") or "").strip()
        work_started_at = cleaned.get("work_started_at")
        work_ended_at = cleaned.get("work_ended_at")
        attachments = []
        if hasattr(self, "files") and self.files is not None:
            attachments = [f for f in self.files.getlist("attachments") if getattr(f, "name", "")]
        if bool(work_started_at) != bool(work_ended_at):
            raise forms.ValidationError("Informe início e fim do trabalho para registrar o tempo apontado.")
        if work_started_at and work_ended_at and work_ended_at < work_started_at:
            self.add_error("work_ended_at", "O fim do trabalho deve ser posterior ao início.")
        if not message and not attachments and not (work_started_at and work_ended_at):
            raise forms.ValidationError("Informe uma mensagem, anexe um arquivo ou registre o tempo trabalhado.")
        cleaned["message"] = message
        return cleaned


class PortalDemandTransferForm(forms.Form):
    target_attendant = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Novo atendente",
        empty_label="Selecione um atendente",
        widget=forms.Select(attrs={"class": "queue-select"}),
    )

    def __init__(self, *args, demand=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.filter(is_active=True).filter(Q(is_system_admin=True) | Q(is_superuser=True)).order_by(
            "nameUser", "username", "id"
        )
        current_attendant_id = getattr(demand, "assigned_to_id", None)
        if current_attendant_id:
            queryset = queryset.exclude(pk=current_attendant_id)
        self.fields["target_attendant"].queryset = queryset
        self.demand = demand

    def clean_target_attendant(self):
        target = self.cleaned_data.get("target_attendant")
        demand = getattr(self, "demand", None)
        if target and demand and getattr(demand, "assigned_to_id", None) == target.id:
            raise forms.ValidationError("Selecione um atendente diferente do atual.")
        return target


class PortalDemandFeedbackForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["feedback_rating"].required = True

    class Meta:
        model = PortalDemand
        fields = ["feedback_rating", "feedback_comment"]
        labels = {
            "feedback_rating": "Como foi o atendimento?",
            "feedback_comment": "Comentário do solicitante",
        }
        widgets = {
            "feedback_rating": forms.Select(attrs={"class": "queue-select"}),
            "feedback_comment": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Conte como foi o atendimento, se resolveu sua necessidade e o que podemos melhorar.",
                }
            ),
        }

    def clean_feedback_comment(self):
        return (self.cleaned_data.get("feedback_comment") or "").strip()


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

    def clean_placeholder(self):
        return (self.cleaned_data.get("placeholder") or "").strip()

    def clean_help_text(self):
        return (self.cleaned_data.get("help_text") or "").strip()

    def clean_initial_option_label(self):
        return (self.cleaned_data.get("initial_option_label") or "").strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("field_type") == PortalDemandCustomField.FIELD_SELECT and not cleaned.get("initial_option_label"):
            self.add_error("initial_option_label", "Informe ao menos a primeira opção para campos do tipo lista.")
        return cleaned


class PortalDemandCustomFieldOptionForm(forms.Form):
    label = forms.CharField(max_length=80, label="Nova opção")

    def clean_label(self):
        return (self.cleaned_data.get("label") or "").strip()
