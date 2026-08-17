from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="tires_dashboard"),
    path("frota/", views.fleet, name="tires_fleet"),
    path("frota/salvar/", views.truck_save, name="tires_truck_save"),
    path("frota/<int:truck_id>/", views.truck_detail, name="tires_truck"),
    path("frota/<int:truck_id>/posicao/", views.slot_action, name="tires_slot_action"),
    path("frota/<int:truck_id>/reposicionar/", views.slot_swap, name="tires_slot_swap"),
    path("estoque/", views.inventory, name="tires_inventory"),
    path("estoque/cadastrar/", views.tire_create, name="tires_tire_create"),
    path("estoque/acao/", views.tire_action, name="tires_tire_action"),
    path("estoque/checar-numeros/", views.tire_check_serials, name="tires_tire_check_serials"),
    path("estoque/<int:tire_id>/", views.tire_detail, name="tires_tire"),
    path("estoque/<int:tire_id>/editar/", views.tire_edit, name="tires_tire_edit"),
    path("modelos/", views.model_list, name="tires_models"),
    path("modelos/salvar/", views.model_save, name="tires_model_save"),
    path("modelos/excluir/", views.model_delete, name="tires_model_delete"),
    path("movimentacoes/", views.movements, name="tires_movements"),
]
