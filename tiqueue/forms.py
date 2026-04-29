from django import forms
from .models import (
    userQueue,
    TaskGroup,
    TaskType,
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
)


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

class UserQueueCreateForm(forms.ModelForm):
    class Meta:
        model = userQueue
        fields = [
            'a_ticket',
            'f_conclusion_rate',
            'a_description',
            'task_group',
            'task_type',
            'd_predicted_date_start',
            't_predicted_time_start',
            'd_predicted_date_end',
            't_predicted_time_end',
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
            'd_predicted_date_start':forms.DateInput(attrs={'type':'date'}),
            'd_predicted_date_end':forms.DateInput(attrs={'type':'date'}),
            't_predicted_time_start':forms.TimeInput(attrs={'type':'time'}),
            't_predicted_time_end':forms.TimeInput(attrs={'type':'time'}),
            'n_queue_position':forms.HiddenInput()
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['task_group'].queryset = TaskGroup.objects.all().order_by('name')
        self.fields['task_type'].queryset = TaskType.objects.select_related('group').order_by('group__name', 'name')
        self.fields['task_group'].required = False
        self.fields['task_type'].required = False

        self.fields['task_group'].widget.attrs.update({'class': 'queue-select'})
        # Replace widget but keep bound choices so create_option receives ModelChoiceIteratorValue (.instance).
        self.fields['task_type'].widget = TaskTypeSelect(attrs={'class': 'queue-select'})
        self.fields['task_type'].widget.choices = self.fields['task_type'].choices


class UserQueueUpdateForm(forms.ModelForm):
    class Meta:
        model = userQueue
        exclude = [
            'user_code',
            'n_queue_position',
            'f_total_predicted_time',
            'f_total_real_time',
            'f_predicted_real_diference',
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
        fields = ["name", "description", "status", "color", "start_date", "end_date"]

        labels = {
            "name": "Projeto",
            "description": "Descricao",
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


class ProjectRoadmapItemForm(forms.ModelForm):
    class Meta:
        model = ProjectRoadmapItem
        fields = ["project", "title", "description", "status", "start_date", "end_date", "sort_order"]

        labels = {
            "project": "Projeto",
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
