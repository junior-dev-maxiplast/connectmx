from django.urls import path

from . import views


urlpatterns = [
    path("logistica/pneus/", views.truck_tire_control_page, name="truck_tire_control"),
    path("logistica/pneus/historico/", views.truck_tire_history_page, name="truck_tire_history"),
    path("logistica/romaneios/", views.logistics_romaneio_page, name="logistics_romaneio"),
    path("logistica/romaneios/ranking/", views.logistics_romaneio_ranking_page, name="logistics_romaneio_ranking"),
    path("logistica/romaneios/api/quick-submit/", views.logistics_romaneio_quick_submit, name="logistics_romaneio_quick_submit"),
    path("almoco/", views.lunch_booking_page, name="lunch_booking"),
    path("almoco/admin/", views.lunch_booking_admin_page, name="lunch_booking_admin"),
    path("sede/login/", views.login_page, name="hqbooking_login"),
    path("sede/login/submit/", views.login_submit, name="hqbooking_login_submit"),
    path("sede/logout/", views.logout_submit, name="hqbooking_logout"),
    path("sede/agenda/", views.calendar_page, name="hqbooking_calendar"),
    path("sede/api/calendar/", views.calendar_data, name="hqbooking_api_calendar"),
    path("sede/api/reserve/", views.reserve_date, name="hqbooking_api_reserve"),
    path("sede/api/cancel/", views.cancel_reservation, name="hqbooking_api_cancel"),
    path("sede/admin/login/", views.admin_login_page, name="hqbooking_admin_login"),
    path("sede/admin/login/submit/", views.admin_login_submit, name="hqbooking_admin_login_submit"),
    path("sede/admin/logout/", views.admin_logout_submit, name="hqbooking_admin_logout"),
    path("sede/admin/painel/", views.admin_panel_page, name="hqbooking_admin_panel"),
    path("sede/admin/api/requests/", views.admin_requests_data, name="hqbooking_admin_requests_data"),
    path("sede/admin/api/requests/<int:reservation_id>/approve/", views.admin_approve_request, name="hqbooking_admin_approve"),
    path("sede/admin/api/requests/<int:reservation_id>/reject/", views.admin_reject_request, name="hqbooking_admin_reject"),
    path("sede/admin/api/environments/", views.admin_environments_data, name="hqbooking_admin_environments_data"),
    path("sede/admin/api/environments/create/", views.admin_environment_create, name="hqbooking_admin_environment_create"),
    path("sede/admin/api/environments/<int:environment_id>/update/", views.admin_environment_update, name="hqbooking_admin_environment_update"),
    path("sede/admin/api/environments/<int:environment_id>/delete/", views.admin_environment_delete, name="hqbooking_admin_environment_delete"),
    path("sede/admin/api/blocks/", views.admin_blocks_data, name="hqbooking_admin_blocks_data"),
    path("sede/admin/api/blocks/create/", views.admin_block_create, name="hqbooking_admin_block_create"),
    path("sede/admin/api/blocks/<int:block_id>/delete/", views.admin_block_delete, name="hqbooking_admin_block_delete"),
]
