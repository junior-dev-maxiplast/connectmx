import json
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    SimulationRomaneioEntry,
    Tire,
    TireMovement,
    Truck,
    TruckModelTemplate,
    TruckTireChange,
    TruckTireChangeHistory,
)
from .views import _extract_romaneio_payload, _insert_simulation_romaneio_oracle


def _sample_structure():
    return [
        {
            "left": [{"name": "DE"}],
            "right": [{"name": "DD"}],
            "spares": [{"name": "Estepe 1"}, {"name": "Estepe 2"}],
        }
    ]


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class TruckTirePagesTests(TestCase):
    def setUp(self):
        self.template = TruckModelTemplate.objects.create(
            name="Modelo Teste",
            axle_count=1,
            wheel_count=4,
            structure_json=json.dumps(_sample_structure()),
        )
        self.truck = Truck.objects.create(
            identifier="CAM-001",
            model_template=self.template,
            tire_count=self.template.wheel_count,
            layout_model="TEMPLATE",
        )

    def test_dashboard_renders(self):
        Tire.objects.create(brand="Goodyear", serial_number="DASH-001", status=Tire.STATUS_STOCK)

        response = self.client.get(reverse("tires_dashboard"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "hqbooking/tires/dashboard.html")
        self.assertContains(response, "tl-kpis")
        self.assertContains(response, "Composi")

    def test_fleet_page_lists_trucks(self):
        response = self.client.get(reverse("tires_fleet"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CAM-001")
        self.assertContains(response, "tl-truck-card")

    def test_fleet_page_filters_by_identifier_and_model(self):
        other_template = TruckModelTemplate.objects.create(
            name="Carreta",
            axle_count=1,
            wheel_count=2,
            structure_json=json.dumps([{"left": [{"name": "DE"}], "right": [{"name": "DD"}], "spares": []}]),
        )
        Truck.objects.create(
            identifier="KAZ-9090",
            model_template=other_template,
            tire_count=other_template.wheel_count,
            layout_model="TEMPLATE",
        )

        by_plate = self.client.get(reverse("tires_fleet"), {"q": "kaz"}, HTTP_HOST="localhost")
        self.assertEqual(by_plate.status_code, 200)
        self.assertContains(by_plate, "KAZ-9090")
        self.assertNotContains(by_plate, "CAM-001")

        by_model = self.client.get(
            reverse("tires_fleet"), {"model": self.template.id}, HTTP_HOST="localhost"
        )
        self.assertEqual(by_model.status_code, 200)
        self.assertContains(by_model, "CAM-001")
        self.assertNotContains(by_model, "KAZ-9090")

        combined = self.client.get(
            reverse("tires_fleet"),
            {"q": "kaz", "model": self.template.id},
            HTTP_HOST="localhost",
        )
        self.assertEqual(combined.status_code, 200)
        self.assertContains(combined, "Nenhum caminh")

    def test_truck_detail_renders_slot_map(self):
        response = self.client.get(reverse("tires_truck", args=[self.truck.id]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-tl-map")
        self.assertContains(response, "tlSlotModal")
        self.assertContains(response, "tlSwapModal")
        # Duas posicoes de eixo mais dois estepes.
        self.assertEqual(response.content.decode().count("data-tl-slot"), 4)

    def test_inventory_page_renders_filters_and_batch_form(self):
        response = self.client.get(reverse("tires_inventory"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tl-filter")
        self.assertContains(response, "tlTireCreateForm")
        self.assertContains(response, "batch_mode")

    def test_models_page_lists_cards_and_exposes_structures_for_editor(self):
        response = self.client.get(reverse("tires_models"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tlModelEditor")
        self.assertContains(response, "tlModelStructures")
        self.assertContains(response, "Modelo Teste")
        self.assertContains(response, "tl-truck-card")
        self.assertContains(response, 'data-tl-model-edit="%s"' % self.template.id)
        self.assertContains(response, "data-tl-model-new")

    def test_models_page_hides_delete_when_trucks_use_the_model(self):
        in_use = self.client.get(reverse("tires_models"), HTTP_HOST="localhost")
        self.assertNotContains(in_use, "tires_model_delete")
        self.assertContains(in_use, "1 caminhão")

        self.truck.delete()
        free = self.client.get(reverse("tires_models"), HTTP_HOST="localhost")
        self.assertContains(free, reverse("tires_model_delete"))

    def test_models_page_autoloads_editor_for_model_in_query(self):
        response = self.client.get(
            reverse("tires_models"), {"model": self.template.id}, HTTP_HOST="localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-tl-autoload="%s"' % self.template.id)

    def test_movements_page_renders(self):
        response = self.client.get(reverse("tires_movements"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hist")

    def test_tire_detail_page_renders(self):
        tire = Tire.objects.create(brand="Michelin", serial_number="FICHA-1", status=Tire.STATUS_STOCK)

        response = self.client.get(reverse("tires_tire", args=[tire.id]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FICHA-1")
        self.assertContains(response, "Linha do tempo")

    def test_legacy_history_url_redirects_to_movements(self):
        response = self.client.get("/logistica/pneus/historico/", HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("tires_movements"), fetch_redirect_response=False)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class TruckTireFlowTests(TestCase):
    def setUp(self):
        self.template = TruckModelTemplate.objects.create(
            name="Modelo Teste",
            axle_count=1,
            wheel_count=4,
            structure_json=json.dumps(_sample_structure()),
        )
        self.truck = Truck.objects.create(
            identifier="CAM-001",
            model_template=self.template,
            tire_count=self.template.wheel_count,
            layout_model="TEMPLATE",
        )
        self.slot_url = reverse("tires_slot_action", args=[self.truck.id])
        self.swap_url = reverse("tires_slot_swap", args=[self.truck.id])
        self.tire_action_url = reverse("tires_tire_action")
        self.tire_create_url = reverse("tires_tire_create")

    def test_can_create_install_and_return_tire_to_stock(self):
        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "create_and_install",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "P-001",
                "new_tire_purchase_value": "1250,50",
                "changed_on": "2026-07-02",
                "odometer_km": "1000",
                "note": "Instalacao inicial",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire = Tire.objects.get(serial_number="P-001")
        self.assertEqual(tire.status, Tire.STATUS_INSTALLED)
        self.assertEqual(tire.current_truck_id, self.truck.id)
        self.assertEqual(tire.current_tire_number, 1)
        self.assertEqual(str(tire.purchase_value), "1250.50")
        self.assertTrue(
            TruckTireChange.objects.filter(
                truck=self.truck, tire_number=1, tire=tire, tire_brand="Goodyear"
            ).exists()
        )
        self.assertTrue(
            TireMovement.objects.filter(
                tire=tire, movement_type=TireMovement.TYPE_INSTALL, truck=self.truck
            ).exists()
        )

        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "move_to_stock",
                "changed_on": "2026-07-03",
                "odometer_km": "1120",
                "note": "Volta para o estoque",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.status, Tire.STATUS_STOCK)
        self.assertIsNone(tire.current_truck_id)
        self.assertFalse(TruckTireChange.objects.filter(truck=self.truck, tire_number=1).exists())
        self.assertTrue(
            TireMovement.objects.filter(
                tire=tire, movement_type=TireMovement.TYPE_TO_STOCK, truck=self.truck
            ).exists()
        )

    def test_can_create_tires_in_batch(self):
        response = self.client.post(
            self.tire_create_url,
            {
                "batch_mode": "paste",
                "brand": "Goodyear",
                "serial_batch": "L-001\nL-002\nL-003",
                "registered_on": "2026-07-09",
                "purchase_value": "980,50",
                "note": "Entrada em lote",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tire.objects.filter(serial_number__in=["L-001", "L-002", "L-003"]).count(), 3)
        self.assertEqual(
            TireMovement.objects.filter(
                tire__serial_number__in=["L-001", "L-002", "L-003"],
                movement_type=TireMovement.TYPE_REGISTER,
            ).count(),
            3,
        )

    def test_can_create_tires_from_generated_sequence_batch(self):
        response = self.client.post(
            self.tire_create_url,
            {
                "batch_mode": "generate",
                "brand": "Pirelli",
                "batch_prefix": "P-",
                "batch_start_number": "7",
                "batch_quantity": "4",
                "batch_pad_length": "3",
                "registered_on": "2026-07-10",
                "purchase_value": "1200,00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Tire.objects.filter(serial_number__in=["P-007", "P-008", "P-009", "P-010"]).count(),
            4,
        )

    def test_initial_load_keeps_the_real_state_of_a_tire_already_on_the_truck(self):
        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "LEGADO-1",
                "new_tire_purchase_value": "1400,00",
                "initial_recap_count": "2",
                "initial_retread_total": "640,00",
                "changed_on": "2026-01-15",
                "odometer_km": "180000",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire = Tire.objects.get(serial_number="LEGADO-1")
        self.assertEqual(tire.status, Tire.STATUS_INSTALLED)
        self.assertEqual(tire.current_truck_id, self.truck.id)
        # Nao zera o historico: os recapes e o custo ja gastos vem junto.
        self.assertEqual(tire.recap_count, 2)
        self.assertEqual(str(tire.total_retread_cost), "640.00")
        # A data informada e a de quando o pneu foi para a posicao, nao hoje.
        self.assertEqual(tire.registered_on.isoformat(), "2026-01-15")

        assignment = TruckTireChange.objects.get(truck=self.truck, tire_number=1)
        self.assertEqual(assignment.changed_on.isoformat(), "2026-01-15")
        self.assertEqual(assignment.odometer_km, 180000)

    def test_initial_load_baseline_makes_the_first_swap_measure_real_mileage(self):
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "LEGADO-2",
                "initial_recap_count": "1",
                "changed_on": "2026-01-01",
                "odometer_km": "100000",
            },
            follow=True,
        )
        substitute = Tire.objects.create(
            brand="Michelin", serial_number="SUBST-1", status=Tire.STATUS_STOCK
        )

        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "install_stock",
                "stock_tire_id": substitute.id,
                "changed_on": "2026-04-01",
                "odometer_km": "130000",
            },
            follow=True,
        )

        history = TruckTireChangeHistory.objects.get(truck=self.truck, tire_number=1, action_type="install")
        # 100.000 -> 130.000 km e 01/01 -> 01/04, contados a partir da carga inicial.
        self.assertEqual(history.run_km, 30000)
        self.assertEqual(history.run_days, 90)
        # A rodagem e do pneu que SAIU, entao a ficha dele precisa mostra-la.
        self.assertEqual(history.previous_tire_code, "LEGADO-2")

        ficha = self.client.get(
            reverse("tires_tire", args=[Tire.objects.get(serial_number="LEGADO-2").id]), HTTP_HOST="localhost"
        )
        self.assertEqual(ficha.status_code, 200)
        self.assertEqual(ficha.context["total_run_km"], 30000)
        self.assertEqual(ficha.context["total_run_days"], 90)
        self.assertIsNotNone(ficha.context["cost_per_km"])

    def test_tire_sheet_does_not_credit_the_previous_tire_mileage(self):
        """A ficha do substituto nao pode herdar a rodagem de quem saiu."""
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "SAIU",
                "changed_on": "2026-01-01",
                "odometer_km": "100000",
            },
            follow=True,
        )
        entrou = Tire.objects.create(brand="Michelin", serial_number="ENTROU", status=Tire.STATUS_STOCK)
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "install_stock",
                "stock_tire_id": entrou.id,
                "changed_on": "2026-04-01",
                "odometer_km": "130000",
            },
            follow=True,
        )

        ficha = self.client.get(reverse("tires_tire", args=[entrou.id]), HTTP_HOST="localhost")
        self.assertEqual(ficha.context["total_run_km"], 0)
        self.assertEqual(ficha.context["total_run_days"], 0)

    def test_renaming_a_tire_keeps_its_closed_cycles_on_the_sheet(self):
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "ANTES",
                "changed_on": "2026-01-01",
                "odometer_km": "100000",
            },
            follow=True,
        )
        substitute = Tire.objects.create(brand="Michelin", serial_number="QUALQUER", status=Tire.STATUS_STOCK)
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "install_stock",
                "stock_tire_id": substitute.id,
                "changed_on": "2026-04-01",
                "odometer_km": "130000",
            },
            follow=True,
        )

        tire = Tire.objects.get(serial_number="ANTES")
        self.client.post(
            reverse("tires_tire_edit", args=[tire.id]),
            {"serial_number": "DEPOIS", "brand": "Goodyear"},
            follow=True,
        )

        ficha = self.client.get(reverse("tires_tire", args=[tire.id]), HTTP_HOST="localhost")
        self.assertEqual(ficha.context["total_run_km"], 30000)

    def test_initial_load_is_rejected_on_an_occupied_position(self):
        occupant = Tire.objects.create(
            brand="Pirelli",
            serial_number="OCUPANTE",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=occupant,
            tire_code=occupant.serial_number,
            tire_brand=occupant.brand,
        )

        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "LEGADO-3",
                "changed_on": "2026-01-15",
            },
            follow=True,
        )

        self.assertFalse(Tire.objects.filter(serial_number="LEGADO-3").exists())
        self.assertEqual(TruckTireChange.objects.get(truck=self.truck, tire_number=1).tire_id, occupant.id)

    def test_initial_load_requires_the_installation_date(self):
        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "LEGADO-4",
            },
            follow=True,
        )

        self.assertFalse(Tire.objects.filter(serial_number="LEGADO-4").exists())

    def test_initial_load_refuses_a_serial_that_already_exists(self):
        Tire.objects.create(brand="Pirelli", serial_number="JA-TEM", status=Tire.STATUS_STOCK)

        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "initial_load",
                "new_tire_brand": "Goodyear",
                "new_tire_serial": "ja-tem",
                "changed_on": "2026-01-15",
            },
            follow=True,
        )

        self.assertEqual(Tire.objects.filter(serial_number__iexact="ja-tem").count(), 1)
        self.assertFalse(TruckTireChange.objects.filter(truck=self.truck, tire_number=1).exists())

    def test_stock_registration_accepts_used_tires_with_previous_recaps(self):
        self.client.post(
            self.tire_create_url,
            {
                "batch_mode": "single",
                "brand": "Bridgestone",
                "serial_number": "USADO-1",
                "recap_count": "2",
                "retread_total": "500,00",
            },
            follow=True,
        )

        tire = Tire.objects.get(serial_number="USADO-1")
        self.assertEqual(tire.recap_count, 2)
        self.assertEqual(str(tire.total_retread_cost), "500.00")

    def test_recap_count_above_the_limit_is_clamped(self):
        self.client.post(
            self.tire_create_url,
            {"batch_mode": "single", "brand": "Pirelli", "serial_number": "USADO-2", "recap_count": "9"},
            follow=True,
        )

        self.assertEqual(Tire.objects.get(serial_number="USADO-2").recap_count, 3)

    def test_batch_registration_reuses_the_existing_brand_spelling(self):
        Tire.objects.create(brand="Goodyear", serial_number="MARCA-BASE", status=Tire.STATUS_STOCK)

        self.client.post(
            self.tire_create_url,
            {"batch_mode": "single", "brand": "  GOODYEAR ", "serial_number": "MARCA-1"},
            follow=True,
        )

        self.assertEqual(Tire.objects.get(serial_number="MARCA-1").brand, "Goodyear")
        self.assertEqual(Tire.objects.values_list("brand", flat=True).distinct().count(), 1)

    def test_installing_a_brand_new_tire_also_normalizes_the_brand(self):
        Tire.objects.create(brand="Michelin", serial_number="MARCA-BASE", status=Tire.STATUS_STOCK)

        self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "create_and_install",
                "new_tire_brand": "michelin",
                "new_tire_serial": "MARCA-2",
                "changed_on": "2026-07-02",
            },
            follow=True,
        )

        self.assertEqual(Tire.objects.get(serial_number="MARCA-2").brand, "Michelin")

    def test_serial_check_endpoint_reports_existing_numbers(self):
        Tire.objects.create(brand="Pirelli", serial_number="DUP-001", status=Tire.STATUS_STOCK)

        response = self.client.post(
            reverse("tires_tire_check_serials"),
            data=json.dumps({"serials": ["dup-001", "DUP-002", "  "]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        # Devolve a grafia que o usuario digitou, para a previa marcar o chip certo.
        self.assertEqual(payload["taken"], ["dup-001"])

    def test_serial_check_endpoint_rejects_invalid_payload(self):
        response = self.client.post(
            reverse("tires_tire_check_serials"),
            data=json.dumps({"serials": "DUP-001"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "error")

    def test_can_edit_tire_and_keep_history(self):
        tire = Tire.objects.create(
            brand="Firestone",
            serial_number="EDIT-ERRADO",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=tire,
            tire_code=tire.serial_number,
            tire_brand=tire.brand,
        )
        TireMovement.objects.create(tire=tire, movement_type=TireMovement.TYPE_REGISTER)

        response = self.client.post(
            reverse("tires_tire_edit", args=[tire.id]),
            {
                "serial_number": "EDIT-CERTO",
                "brand": "Firestone",
                "purchase_value": "980,50",
                "registered_on": "2026-07-01",
                "note": "numero corrigido",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.serial_number, "EDIT-CERTO")
        self.assertEqual(str(tire.purchase_value), "980.50")
        self.assertEqual(tire.notes, "numero corrigido")
        # O historico continua de pe e o mapa do caminhao acompanha o novo numero.
        self.assertEqual(TireMovement.objects.filter(tire=tire).count(), 1)
        self.assertEqual(
            TruckTireChange.objects.get(truck=self.truck, tire_number=1).tire_code, "EDIT-CERTO"
        )

    def test_cannot_edit_tire_into_an_existing_serial(self):
        Tire.objects.create(brand="Pirelli", serial_number="JA-EXISTE", status=Tire.STATUS_STOCK)
        tire = Tire.objects.create(brand="Pirelli", serial_number="OUTRO", status=Tire.STATUS_STOCK)

        response = self.client.post(
            reverse("tires_tire_edit", args=[tire.id]),
            {"serial_number": "ja-existe", "brand": "Pirelli"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.serial_number, "OUTRO")

    def test_can_delete_stock_tire_permanently(self):
        tire = Tire.objects.create(brand="Michelin", serial_number="P-DELETE", status=Tire.STATUS_STOCK)

        response = self.client.post(
            self.tire_action_url,
            {"tire_id": tire.id, "action": "delete_permanently"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tire.objects.filter(pk=tire.id).exists())

    def test_cannot_delete_installed_tire_permanently(self):
        tire = Tire.objects.create(
            brand="Pirelli",
            serial_number="P-NODELETE",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=tire,
            tire_code=tire.serial_number,
            tire_brand=tire.brand,
        )

        response = self.client.post(
            self.tire_action_url,
            {"tire_id": tire.id, "action": "delete_permanently"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Tire.objects.filter(pk=tire.id).exists())

    def test_installing_stock_tire_moves_previous_tire_to_stock(self):
        current_tire = Tire.objects.create(
            brand="Pirelli",
            serial_number="P-ATUAL",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=current_tire,
            tire_code=current_tire.serial_number,
            tire_brand=current_tire.brand,
        )
        stock_tire = Tire.objects.create(
            brand="Michelin", serial_number="P-ESTOQUE", status=Tire.STATUS_STOCK
        )

        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "install_stock",
                "stock_tire_id": stock_tire.id,
                "changed_on": "2026-07-04",
                "odometer_km": "1500",
                "note": "Troca com pneu do estoque",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        current_tire.refresh_from_db()
        stock_tire.refresh_from_db()

        self.assertEqual(current_tire.status, Tire.STATUS_STOCK)
        self.assertEqual(stock_tire.status, Tire.STATUS_INSTALLED)
        self.assertEqual(stock_tire.current_truck_id, self.truck.id)
        self.assertEqual(stock_tire.current_tire_number, 1)

        assignment = TruckTireChange.objects.get(truck=self.truck, tire_number=1)
        self.assertEqual(assignment.tire_id, stock_tire.id)
        self.assertEqual(assignment.tire_brand, "Michelin")

    def test_retread_is_limited_to_three_cycles(self):
        tire = Tire.objects.create(
            brand="Bridgestone",
            serial_number="P-RECAPE",
            status=Tire.STATUS_STOCK,
            recap_count=2,
        )

        self.client.post(self.tire_action_url, {"tire_id": tire.id, "action": "retread"}, follow=True)
        tire.refresh_from_db()
        self.assertEqual(tire.recap_count, 3)

        self.client.post(self.tire_action_url, {"tire_id": tire.id, "action": "retread"}, follow=True)
        tire.refresh_from_db()
        self.assertEqual(tire.recap_count, 3)

    def test_can_send_tire_to_retread_and_return_to_stock(self):
        tire = Tire.objects.create(
            brand="Firestone",
            serial_number="P-RETORNO",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=tire,
            tire_code=tire.serial_number,
            tire_brand=tire.brand,
        )

        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 1,
                "action_mode": "send_current_to_retread",
                "changed_on": "2026-07-05",
                "odometer_km": "1800",
                "note": "Mandado para recapagem",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.status, Tire.STATUS_RETREADING)
        self.assertIsNone(tire.current_truck_id)
        self.assertFalse(TruckTireChange.objects.filter(truck=self.truck, tire_number=1).exists())
        self.assertTrue(
            TireMovement.objects.filter(tire=tire, movement_type=TireMovement.TYPE_TO_RETREAD).exists()
        )

        response = self.client.post(
            self.tire_action_url,
            {"tire_id": tire.id, "action": "return_from_retread", "cost": "320,75"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.status, Tire.STATUS_STOCK)
        self.assertEqual(tire.recap_count, 1)
        self.assertEqual(str(tire.last_retread_cost), "320.75")
        self.assertEqual(str(tire.total_retread_cost), "320.75")
        self.assertTrue(
            TireMovement.objects.filter(tire=tire, movement_type=TireMovement.TYPE_FROM_RETREAD).exists()
        )

    def test_can_install_spare_directly_on_position(self):
        target_tire = Tire.objects.create(
            brand="Continental",
            serial_number="P-ALVO",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        spare_tire = Tire.objects.create(
            brand="Goodyear",
            serial_number="P-ESTEPE",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=3,
            current_slot_label="Estepe 1",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=target_tire,
            tire_code=target_tire.serial_number,
            tire_brand=target_tire.brand,
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=3,
            tire=spare_tire,
            tire_code=spare_tire.serial_number,
            tire_brand=spare_tire.brand,
        )

        response = self.client.post(
            self.slot_url,
            {
                "tire_number": 3,
                "target_tire_number": 1,
                "action_mode": "install_spare_to_position",
                "changed_on": "2026-07-06",
                "odometer_km": "2000",
                "note": "Estepe assumiu a frente",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target_tire.refresh_from_db()
        spare_tire.refresh_from_db()

        self.assertEqual(target_tire.status, Tire.STATUS_STOCK)
        self.assertEqual(spare_tire.status, Tire.STATUS_INSTALLED)
        self.assertEqual(spare_tire.current_truck_id, self.truck.id)
        self.assertEqual(spare_tire.current_tire_number, 1)
        self.assertEqual(spare_tire.current_slot_label, "DE")
        self.assertFalse(TruckTireChange.objects.filter(truck=self.truck, tire_number=3).exists())

        assignment = TruckTireChange.objects.get(truck=self.truck, tire_number=1)
        self.assertEqual(assignment.tire_id, spare_tire.id)
        self.assertEqual(assignment.tire_brand, "Goodyear")

    def test_can_reposition_tires_between_truck_slots(self):
        front_tire = Tire.objects.create(
            brand="Goodyear",
            serial_number="P-FRENTE",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=1,
            current_slot_label="DE",
        )
        rear_tire = Tire.objects.create(
            brand="Michelin",
            serial_number="P-TRASEIRO",
            status=Tire.STATUS_INSTALLED,
            current_truck=self.truck,
            current_tire_number=2,
            current_slot_label="DD",
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=1,
            tire=front_tire,
            tire_code=front_tire.serial_number,
            tire_brand=front_tire.brand,
            changed_on="2026-07-01",
            odometer_km=1000,
        )
        TruckTireChange.objects.create(
            truck=self.truck,
            tire_number=2,
            tire=rear_tire,
            tire_code=rear_tire.serial_number,
            tire_brand=rear_tire.brand,
            changed_on="2026-07-01",
            odometer_km=1000,
        )

        response = self.client.post(
            self.swap_url,
            {
                "source_tire_number": 1,
                "target_tire_number": 2,
                "changed_on": "2026-07-07",
                "odometer_km": "2500",
                "note": "Permuta operacional",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        front_tire.refresh_from_db()
        rear_tire.refresh_from_db()

        self.assertEqual(front_tire.current_tire_number, 2)
        self.assertEqual(front_tire.current_slot_label, "DD")
        self.assertEqual(rear_tire.current_tire_number, 1)
        self.assertEqual(rear_tire.current_slot_label, "DE")

        self.assertEqual(TruckTireChange.objects.get(truck=self.truck, tire_number=2).tire_id, front_tire.id)
        self.assertEqual(TruckTireChange.objects.get(truck=self.truck, tire_number=1).tire_id, rear_tire.id)

        self.assertEqual(
            TireMovement.objects.filter(
                tire=front_tire,
                truck=self.truck,
                tire_number=2,
                movement_type=TireMovement.TYPE_REPOSITION,
            ).count(),
            1,
        )
        self.assertEqual(
            TireMovement.objects.filter(
                tire=rear_tire,
                truck=self.truck,
                tire_number=1,
                movement_type=TireMovement.TYPE_REPOSITION,
            ).count(),
            1,
        )

    def test_model_save_creates_template_from_structure(self):
        response = self.client.post(
            reverse("tires_model_save"),
            {
                "name": "Carreta 3 eixos",
                "structure_json": json.dumps(
                    [
                        {
                            "left": [{"name": "DE"}],
                            "right": [{"name": "DD"}],
                            "spares": [{"name": "Estepe 1"}],
                        },
                        {
                            "left": [{"name": "1EE"}, {"name": "1EI"}],
                            "right": [{"name": "1DI"}, {"name": "1DE"}],
                            "spares": [],
                        },
                    ]
                ),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        template = TruckModelTemplate.objects.get(name="Carreta 3 eixos")
        self.assertEqual(template.axle_count, 2)
        self.assertEqual(template.wheel_count, 7)

    def test_model_delete_is_blocked_while_trucks_use_it(self):
        response = self.client.post(
            reverse("tires_model_delete"),
            {"model_id": self.template.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(TruckModelTemplate.objects.filter(pk=self.template.id).exists())

    def test_truck_save_creates_truck_from_model(self):
        response = self.client.post(
            reverse("tires_truck_save"),
            {"identifier": "CAM-999", "model_id": self.template.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        truck = Truck.objects.get(identifier="CAM-999")
        self.assertEqual(truck.model_template_id, self.template.id)
        self.assertEqual(truck.tire_count, self.template.wheel_count)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class LogisticsRomaneioTests(TestCase):
    @staticmethod
    def _mock_oracle_insert_success(entry):
        entry.sequence_record = "124"
        return None

    def test_romaneio_page_renders(self):
        response = self.client.get(reverse("logistics_romaneio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Novo lançamento de romaneio")

    def test_romaneio_page_uses_logistics_class(self):
        response = self.client.get(reverse("logistics_romaneio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "logistics-page")

    def test_romaneio_mobile_page_renders_scan_flow(self):
        response = self.client.get(reverse("logistics_romaneio_mobile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matrícula")
        self.assertContains(response, "Salvar Leitura")
        # A câmera abre por um dos quatro estágios: não existe botão de leitura
        # sem etapa, porque o registro sairia sem classificação no ERP.
        for etapa in ("Separar", "Guardar", "Paletizar", "Carregar"):
            self.assertContains(response, etapa)
        # A tela grava pela mesma rota de envio rápido usada pelo leitor de mesa.
        self.assertContains(response, reverse("logistics_romaneio_quick_submit"))

    def test_romaneio_mobile_page_has_camera_reading_only(self):
        """A leitura é só pela câmera: nada de digitação ou captura de coletor."""
        response = self.client.get(reverse("logistics_romaneio_mobile"))
        self.assertContains(response, "<video")
        self.assertNotContains(response, "textarea")
        self.assertNotContains(response, "coletor")

    @patch("hqbooking.views._fetch_simulation_romaneio_ranking_data")
    def test_romaneio_ranking_page_renders(self, ranking_mock):
        ranking_mock.return_value = {
            "ranking_rows": [
                {
                    "rank": 1,
                    "user_code": "7",
                    "total_romaneios": 12,
                    "total_volumes": 44,
                    "total_peso": Decimal("3250.4500"),
                }
            ],
            "timeline": [
                {
                    "date": timezone.datetime(2026, 7, 7).date(),
                    "total_romaneios": 12,
                    "total_volumes": 44,
                    "total_peso": Decimal("3250.4500"),
                }
            ],
            "summary": {
                "total_romaneios": 12,
                "total_volumes": 44,
                "total_peso": Decimal("3250.4500"),
                "total_users": 1,
                "start_date": timezone.datetime(2026, 7, 3).date(),
                "end_date": timezone.datetime(2026, 7, 7).date(),
            },
            "top_by_count": {
                "rank": 1,
                "user_code": "7",
                "total_romaneios": 12,
                "total_volumes": 44,
                "total_peso": Decimal("3250.4500"),
            },
            "top_by_weight": {
                "rank": 1,
                "user_code": "7",
                "total_romaneios": 12,
                "total_volumes": 44,
                "total_peso": Decimal("3250.4500"),
            },
            "top_by_volume": {
                "rank": 1,
                "user_code": "7",
                "total_romaneios": 12,
                "total_volumes": 44,
                "total_peso": Decimal("3250.4500"),
            },
        }
        response = self.client.get(reverse("logistics_romaneio_ranking"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ranking de Romaneios")
        self.assertContains(response, "Usu")
        ranking_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_romaneio_post_creates_local_log_and_marks_success(self, insert_mock):
        response = self.client.post(
            reverse("logistics_romaneio"),
            {
                "barcode_payload": "1|2|8|1540,250|9001|103",
                "company_code": "1",
                "branch_code": "2",
                "user_code": "9001",
                "volume_quantity": "8",
                "romaneio_weight": "1540,250",
                "package_code": "9001",
                "address_code": "103",
                "record_type": "1",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 1)
        entry = SimulationRomaneioEntry.objects.first()
        self.assertEqual(entry.sync_status, SimulationRomaneioEntry.SYNC_SUCCESS)
        self.assertEqual(entry.company_code, "1")
        self.assertEqual(entry.branch_code, "2")
        self.assertEqual(entry.sequence_record, "124")
        self.assertEqual(entry.volume_quantity, 8)
        self.assertEqual(str(entry.romaneio_weight), "1540.250")
        self.assertEqual(entry.user_code, "9001")
        self.assertEqual(entry.package_code, "9001")
        self.assertEqual(entry.address_code, "103")
        insert_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", return_value="Falha Oracle")
    def test_romaneio_post_marks_error_when_oracle_fails(self, insert_mock):
        response = self.client.post(
            reverse("logistics_romaneio"),
            {
                "company_code": "1",
                "branch_code": "2",
                "user_code": "9001",
                "volume_quantity": "8",
                "romaneio_weight": "1540,250",
                "package_code": "9002",
                "address_code": "104",
                "record_type": "2",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 1)
        entry = SimulationRomaneioEntry.objects.first()
        self.assertEqual(entry.sync_status, SimulationRomaneioEntry.SYNC_ERROR)
        self.assertEqual(entry.sync_message, "Falha Oracle")
        insert_mock.assert_called_once()

    def test_romaneio_post_requires_package_and_address(self):
        """Os dois campos novos são obrigatórios no cadastro manual da tela de mesa."""
        response = self.client.post(
            reverse("logistics_romaneio"),
            {
                "company_code": "1",
                "branch_code": "2",
                "user_code": "9001",
                "volume_quantity": "8",
                "romaneio_weight": "1540,250",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SimulationRomaneioEntry.objects.exists())
        self.assertContains(response, "código do pallet")

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_romaneio_quick_submit_creates_entry(self, insert_mock):
        response = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps(
                {
                    "barcode_payload": "1/2/8/1540,250/9010/201",
                    "user_code": "9001",
                    "record_type": 1,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 1)
        entry = SimulationRomaneioEntry.objects.first()
        self.assertEqual(entry.company_code, "1")
        self.assertEqual(entry.branch_code, "2")
        self.assertEqual(entry.user_code, "9001")
        self.assertEqual(entry.sequence_record, "124")
        self.assertEqual(entry.package_code, "9010")
        self.assertEqual(entry.address_code, "201")
        insert_mock.assert_called_once()

    def test_romaneio_quick_submit_requires_user(self):
        response = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps(
                {
                    "barcode_payload": "1/2/8/1540,250/9010/201",
                    "user_code": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["status"], "error")

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_romaneio_quick_submit_rejects_same_package_twice(self, insert_mock):
        """Regra de negócio: cada embalagem só entra uma vez, não importa a matrícula."""
        first = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps({"barcode_payload": "1/2/8/1540,250/9088/101", "user_code": "9001", "record_type": 1}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps({"barcode_payload": "1/2/8/1540,250/9088/101", "user_code": "9002", "record_type": 1}),
            content_type="application/json",
        )

        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertEqual(payload["status"], "duplicate")
        self.assertIn("9088", payload["message"])
        self.assertIn("9001", payload["message"])

        self.assertEqual(SimulationRomaneioEntry.objects.count(), 2)
        duplicate_entry = SimulationRomaneioEntry.objects.order_by("-id").first()
        self.assertEqual(duplicate_entry.sync_status, SimulationRomaneioEntry.SYNC_DUPLICATE)
        self.assertEqual(duplicate_entry.user_code, "9002")
        # A segunda tentativa nem chega a tocar no Oracle: a checagem é local.
        insert_mock.assert_called_once()

    def test_romaneio_quick_submit_rejects_non_numeric_package_code(self):
        """USU_NUMEMB é NUMBER no Oracle: um código com letra tem que ser recusado
        antes de chegar perto do INSERT, não estourar lá com ORA-01722."""
        response = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps({"barcode_payload": "1/2/8/1540,250/PLT-04521/101", "user_code": "9001", "record_type": 1}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("numérico", response.json()["message"])
        self.assertFalse(SimulationRomaneioEntry.objects.exists())

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_romaneio_quick_submit_normalizes_leading_zeros_for_duplicate_check(self, insert_mock):
        """"004521" e "4521" são a mesma embalagem pro Oracle (NUMBER não guarda
        zero à esquerda) — a checagem de duplicidade precisa enxergar isso também."""
        first = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps({"barcode_payload": "1/2/8/1540,250/004521/101", "user_code": "9001", "record_type": 1}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["entry"]["package_code"], "4521")

        second = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps({"barcode_payload": "1/2/8/1540,250/4521/101", "user_code": "9002", "record_type": 1}),
            content_type="application/json",
        )

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["status"], "duplicate")
        insert_mock.assert_called_once()


class MobileApiTests(TestCase):
    """API consumida pelo app Expo em `connectmx-mobile/`."""

    @staticmethod
    def _mock_oracle_insert_success(entry):
        entry.sequence_record = "512"
        return None

    def _post(self, payload, **extra):
        # A etapa da contagem é obrigatória em todo envio. Os testes que não
        # estão medindo essa regra herdam "separar" daqui, para o corpo de cada
        # um continuar mostrando só o que aquele teste realmente exercita; quem
        # precisa de outra etapa (ou de nenhuma) sobrescreve no próprio payload.
        body = {"record_type": SimulationRomaneioEntry.STAGE_SEPARAR, **payload}
        return self.client.post(
            reverse("mobile_api_romaneio_create"),
            data=json.dumps(body),
            content_type="application/json",
            **extra,
        )

    def test_ping_reports_service(self):
        response = self.client.get(reverse("mobile_api_ping"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["app"], "connectmx-mobile")

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_create_from_barcode(self, insert_mock):
        response = self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/100/1"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["entry"]["volume_quantity"], 18)
        self.assertEqual(payload["entry"]["source"], "leitura")
        self.assertEqual(payload["entry"]["package_code"], "100")
        self.assertEqual(payload["entry"]["address_code"], "1")

        entry = SimulationRomaneioEntry.objects.get()
        self.assertEqual(entry.user_code, "77")
        self.assertEqual(entry.romaneio_weight, Decimal("1250.500"))
        self.assertEqual(entry.sync_status, SimulationRomaneioEntry.SYNC_SUCCESS)
        insert_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_create_from_manual_fields(self, insert_mock):
        """Caminho secundário do app: sem leitura, os campos vêm digitados."""
        response = self._post(
            {
                "user_code": "77",
                "company_code": "1",
                "branch_code": "2",
                "volume_quantity": "9",
                "romaneio_weight": "812,250",
                "package_code": "200",
                "address_code": "2",
            }
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["entry"]["source"], "manual")

        entry = SimulationRomaneioEntry.objects.get()
        self.assertEqual(entry.volume_quantity, 9)
        self.assertEqual(entry.romaneio_weight, Decimal("812.250"))
        self.assertEqual(entry.package_code, "200")
        self.assertEqual(entry.address_code, "2")
        self.assertIsNone(entry.barcode_payload)
        insert_mock.assert_called_once()

    def test_create_requires_user_code(self):
        response = self._post({"barcode_payload": "1/2/18/1250,500/100/1"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "error")
        self.assertFalse(SimulationRomaneioEntry.objects.exists())

    def test_create_requires_package_code_on_manual_fields(self):
        response = self._post(
            {
                "user_code": "77",
                "company_code": "1",
                "branch_code": "2",
                "volume_quantity": "9",
                "romaneio_weight": "812,250",
                "address_code": "2",
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("código do pallet", response.json()["message"])
        self.assertFalse(SimulationRomaneioEntry.objects.exists())

    def test_create_rejects_unreadable_barcode(self):
        response = self._post({"user_code": "77", "barcode_payload": "somente-um-campo"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("6 campos", response.json()["message"])

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", return_value="Oracle fora do ar")
    def test_create_reports_sync_error_with_entry(self, insert_mock):
        response = self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/100/1"})

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertEqual(payload["status"], "sync_error")
        self.assertEqual(payload["entry"]["sync_status"], SimulationRomaneioEntry.SYNC_ERROR)
        insert_mock.assert_called_once()

    @patch.dict("os.environ", {"CONNECTMX_MOBILE_API_KEY": "chave-secreta"})
    def test_key_is_required_when_configured(self):
        self.assertEqual(self._post({"user_code": "77"}).status_code, 401)

        response = self.client.get(reverse("mobile_api_ping"), HTTP_X_CONNECTMX_KEY="chave-secreta")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["requires_key"])

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_list_filters_by_user_code(self, insert_mock):
        self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/301/3"})
        self._post({"user_code": "88", "barcode_payload": "1/2/20/1300,000/302/4"})

        response = self.client.get(reverse("mobile_api_romaneio_list"), {"user_code": "77"})

        self.assertEqual(response.status_code, 200)
        entries = response.json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["user_code"], "77")

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_client_reference_makes_resend_idempotent(self, insert_mock):
        """O reenvio automático do app não pode dobrar a contagem de pallets.

        Quando o INSERT deu certo mas a resposta não chegou ao celular, a fila
        local tenta de novo com o mesmo `client_reference`.
        """
        body = {
            "user_code": "77",
            "barcode_payload": "1/2/18/1250,500/100/1",
            "client_reference": "leitura-abc-123",
        }

        first = self._post(body)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn("duplicate", first.json())

        second = self._post(body)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])

        self.assertEqual(SimulationRomaneioEntry.objects.count(), 1)
        # A segunda chamada nem chega a tocar no Oracle.
        insert_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", return_value="Oracle fora do ar")
    def test_failed_entry_does_not_block_a_later_resend(self, insert_mock):
        """Uma tentativa que falhou no Oracle precisa poder ser reenviada."""
        body = {
            "user_code": "77",
            "barcode_payload": "1/2/18/1250,500/100/1",
            "client_reference": "leitura-que-falhou",
        }

        self.assertEqual(self._post(body).status_code, 502)
        # Sem sucesso registrado, o reenvio tenta de novo em vez de dar duplicado.
        self.assertEqual(self._post(body).status_code, 502)
        self.assertEqual(insert_mock.call_count, 2)

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_create_rejects_same_package_twice_even_without_client_reference(self, insert_mock):
        """Regra de negócio central: a embalagem é a chave, não o client_reference.

        Sem `client_reference` (como manda a tela web) o mecanismo de reenvio
        idempotente nem entra em ação — quem barra a segunda leitura do mesmo
        pallet é a checagem por `package_code`, e vale mesmo com outra matrícula.
        """
        first = self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/9088/1"})
        self.assertEqual(first.status_code, 200)

        second = self._post({"user_code": "88", "barcode_payload": "1/2/18/1250,500/9088/1"})
        self.assertEqual(second.status_code, 409)
        payload = second.json()
        self.assertEqual(payload["status"], "duplicate_package")
        self.assertIn("9088", payload["message"])
        self.assertIn("77", payload["message"])

        self.assertEqual(SimulationRomaneioEntry.objects.count(), 2)
        self.assertEqual(
            SimulationRomaneioEntry.objects.order_by("-id").first().sync_status,
            SimulationRomaneioEntry.SYNC_DUPLICATE,
        )
        insert_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_entries_without_client_reference_are_never_deduplicated(self, insert_mock):
        """A tela web não manda o identificador: dois pallets diferentes são dois lançamentos."""
        first = self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/401/1"})
        second = self._post({"user_code": "77", "barcode_payload": "1/2/18/1250,500/402/1"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 2)
        self.assertEqual(insert_mock.call_count, 2)


class RomaneioStageTests(TestCase):
    """Os quatro estágios da contagem (USU_TIPREG).

    O que estes testes protegem é uma regra que não é óbvia lendo o código: a
    mesma embalagem é contada quatro vezes ao longo do fluxo do galpão, então
    "pallet repetido" só é erro dentro da mesma etapa.
    """

    @staticmethod
    def _oracle_ok(entry):
        entry.sequence_record = "900"
        return None

    def _post(self, payload):
        body = {"user_code": "77", **payload}
        return self.client.post(
            reverse("mobile_api_romaneio_create"),
            data=json.dumps(body),
            content_type="application/json",
        )

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_mesmo_pallet_avanca_pelas_quatro_etapas(self, insert_mock):
        """O caso que motivou a mudança: separar, guardar, paletizar e carregar
        o MESMO pallet são quatro contagens legítimas, não três duplicidades."""
        for etapa in (1, 2, 3, 4):
            response = self._post(
                {"barcode_payload": "1/2/18/1250,500/5500/103", "record_type": etapa}
            )
            self.assertEqual(
                response.status_code,
                200,
                msg=f"a etapa {etapa} do mesmo pallet foi recusada",
            )
            self.assertEqual(response.json()["entry"]["record_type"], etapa)

        self.assertEqual(SimulationRomaneioEntry.objects.count(), 4)
        self.assertEqual(insert_mock.call_count, 4)
        self.assertEqual(
            sorted(SimulationRomaneioEntry.objects.values_list("record_type", flat=True)),
            [1, 2, 3, 4],
        )

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_recontagem_na_mesma_etapa_continua_recusada(self, insert_mock):
        """Avançar de etapa é esperado; reler na mesma etapa segue sendo erro."""
        primeira = self._post({"barcode_payload": "1/2/18/1250,500/5501/103", "record_type": 2})
        self.assertEqual(primeira.status_code, 200)

        segunda = self._post({"barcode_payload": "1/2/18/1250,500/5501/103", "record_type": 2})
        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.json()["status"], "duplicate_package")
        # A mensagem diz em que etapa o pallet já entrou: sem isso, quem está no
        # galpão não sabe se deve seguir para a próxima ou avisar o supervisor.
        self.assertIn("Guardar", segunda.json()["message"])
        self.assertEqual(insert_mock.call_count, 1)

    def test_envio_sem_etapa_e_recusado(self):
        response = self._post({"barcode_payload": "1/2/18/1250,500/5502/103"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("etapa", response.json()["message"].lower())
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 0)

    def test_etapa_fora_das_quatro_e_recusada(self):
        for invalida in (0, 5, 99, -1, "separar", "", None):
            with self.subTest(record_type=invalida):
                response = self._post(
                    {"barcode_payload": "1/2/18/1250,500/5503/103", "record_type": invalida}
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 0)

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_etapa_tambem_vale_para_o_lancamento_digitado(self, insert_mock):
        """A tela Manual do app manda os campos na mão, mas a etapa é a mesma regra."""
        response = self._post(
            {
                "company_code": "1",
                "branch_code": "2",
                "volume_quantity": "9",
                "romaneio_weight": "812,250",
                "package_code": "5504",
                "address_code": "77",
                "record_type": 4,
            }
        )
        self.assertEqual(response.status_code, 200)
        entry = SimulationRomaneioEntry.objects.get()
        self.assertEqual(entry.record_type, 4)
        self.assertIsNone(entry.barcode_payload)

    def test_insert_no_oracle_leva_a_coluna_usu_tipreg(self):
        """A etapa precisa chegar ao ERP, não só ao banco local."""
        entry = SimulationRomaneioEntry.objects.create(
            company_code="1",
            branch_code="2",
            sequence_record="",
            user_code="77",
            generated_date=timezone.localdate(),
            generated_time=timezone.localtime().time().replace(microsecond=0),
            volume_quantity=8,
            romaneio_weight=Decimal("1540.250"),
            package_code="5505",
            address_code="103",
            record_type=3,
        )

        cursor = MagicMock()
        cursor.fetchone.return_value = [41]
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNone(erro)
        sql, parametros = cursor.execute.call_args[0]
        self.assertIn("USU_TIPREG", sql)
        self.assertIn(":tipo_registro", sql)
        self.assertEqual(parametros["tipo_registro"], 3)
        # A sequência continua saindo do MAX da tabela, como antes.
        self.assertEqual(parametros["sequencia_registro"], 42)

    def test_coluna_ausente_no_oracle_vira_instrucao_legivel(self):
        """Enquanto USU_TIPREG não existir, o erro precisa dizer o que fazer."""
        entry = SimulationRomaneioEntry.objects.create(
            company_code="1",
            branch_code="2",
            sequence_record="",
            user_code="77",
            generated_date=timezone.localdate(),
            generated_time=timezone.localtime().time().replace(microsecond=0),
            volume_quantity=8,
            romaneio_weight=Decimal("1540.250"),
            package_code="5506",
            address_code="103",
            record_type=1,
        )

        cursor = MagicMock()
        cursor.fetchone.return_value = [0]
        cursor.execute.side_effect = [
            None,  # o SELECT da sequência passa
            Exception('ORA-00904: "USU_TIPREG": identificador invalido'),
        ]
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNotNone(erro)
        self.assertIn("USU_TIPREG", erro)
        self.assertIn("ALTER TABLE", erro)
        self.assertNotIn("ORA-00904", erro)

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_cada_etapa_pode_ser_de_um_colaborador_diferente(self, insert_mock):
        """O pallet atravessa o galpão trocando de mãos.

        Quem separa raramente é quem carrega. Como a recusa de recontagem olha
        o par pallet+etapa e não a matrícula, um colaborador nunca bloqueia o
        outro — e cada linha guarda quem fez aquela etapa, que é o que torna o
        histórico útil para saber onde o pallet parou e com quem.
        """
        equipe = {1: "1001", 2: "1002", 3: "1003", 4: "1004"}
        for etapa, matricula in equipe.items():
            response = self.client.post(
                reverse("mobile_api_romaneio_create"),
                data=json.dumps(
                    {
                        "user_code": matricula,
                        "record_type": etapa,
                        "barcode_payload": "1/2/18/1250,500/6100/103",
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, msg=f"etapa {etapa} recusada")

        historico = list(
            SimulationRomaneioEntry.objects.filter(package_code="6100")
            .order_by("record_type")
            .values_list("record_type", "user_code")
        )
        self.assertEqual(historico, [(1, "1001"), (2, "1002"), (3, "1003"), (4, "1004")])

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_historico_do_pallet_guarda_quando_cada_etapa_aconteceu(self, insert_mock):
        """As quatro linhas são eventos, não versões de um mesmo registro.

        Nenhuma sobrescreve a anterior: o que interessa é justamente poder
        dizer que o pallet foi separado às 9h e carregado às 15h.
        """
        for etapa in (1, 2, 3, 4):
            self._post({"barcode_payload": "1/2/18/1250,500/6200/103", "record_type": etapa})

        linhas = SimulationRomaneioEntry.objects.filter(package_code="6200")
        self.assertEqual(linhas.count(), 4)
        # Cada etapa guarda o próprio instante e a própria leitura.
        self.assertEqual(
            linhas.filter(sync_status=SimulationRomaneioEntry.SYNC_SUCCESS).count(), 4
        )
        for linha in linhas:
            self.assertIsNotNone(linha.generated_date)
            self.assertIsNotNone(linha.generated_time)
            self.assertIsNotNone(linha.synced_at)

    def test_sequencia_conta_eventos_do_proprio_pallet(self):
        """O SELECT que decide o USU_SEQCON precisa filtrar pela embalagem.

        Sem o filtro por USU_NUMEMB o número vira um contador corrido da filial
        e o pallet 5500 receberia 41, 87, 102 e 155 em vez de 1, 2, 3 e 4 — a
        chave primária continuaria válida, mas o número deixaria de dizer
        quantas etapas aquela embalagem já cumpriu.
        """
        entry = SimulationRomaneioEntry.objects.create(
            company_code="1",
            branch_code="2",
            sequence_record="",
            user_code="77",
            generated_date=timezone.localdate(),
            generated_time=timezone.localtime().time().replace(microsecond=0),
            volume_quantity=8,
            romaneio_weight=Decimal("1540.250"),
            package_code="6300",
            address_code="103",
            record_type=2,
        )

        cursor = MagicMock()
        cursor.fetchone.return_value = [1]  # o pallet já tinha o evento 1
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNone(erro)

        sql_do_select, parametros_do_select = cursor.execute.call_args_list[0][0]
        self.assertIn("MAX(USU_SEQCON)", sql_do_select)
        self.assertIn("USU_NUMEMB", sql_do_select)
        self.assertEqual(parametros_do_select["embalagem"], 6300)

        _sql_do_insert, parametros_do_insert = cursor.execute.call_args_list[1][0]
        self.assertEqual(parametros_do_insert["sequencia_registro"], 2)
        self.assertEqual(entry.sequence_record, "2")

    def test_primeiro_evento_de_um_pallet_novo_comeca_em_um(self):
        entry = SimulationRomaneioEntry.objects.create(
            company_code="1",
            branch_code="2",
            sequence_record="",
            user_code="77",
            generated_date=timezone.localdate(),
            generated_time=timezone.localtime().time().replace(microsecond=0),
            volume_quantity=8,
            romaneio_weight=Decimal("1540.250"),
            package_code="6301",
            address_code="103",
            record_type=1,
        )

        cursor = MagicMock()
        cursor.fetchone.return_value = [0]  # NVL devolve 0 quando não há linhas
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            self.assertIsNone(_insert_simulation_romaneio_oracle(entry))

        self.assertEqual(entry.sequence_record, "1")

    def _entry_para_oracle(self, package_code, record_type=1):
        return SimulationRomaneioEntry.objects.create(
            company_code="1",
            branch_code="2",
            sequence_record="",
            user_code="77",
            generated_date=timezone.localdate(),
            generated_time=timezone.localtime().time().replace(microsecond=0),
            volume_quantity=8,
            romaneio_weight=Decimal("1540.250"),
            package_code=package_code,
            address_code="103",
            record_type=record_type,
        )

    def test_colisao_de_sequencia_e_refeita_com_o_numero_recalculado(self):
        """Duas leituras do mesmo pallet no mesmo instante não podem travar uma.

        O MAX+1 é lido antes de gravar, então dois envios simultâneos do mesmo
        pallet podem escolher o mesmo número e o segundo bate na chave primária.
        Como o app trata erro do servidor como recusa definitiva, sem esta
        repetição a leitura ficaria parada esperando alguém decidir — por uma
        colisão que some ao reler o MAX.
        """
        entry = self._entry_para_oracle("6400", record_type=3)

        cursor = MagicMock()
        # O MAX sobe entre uma tentativa e outra: o envio concorrente gravou.
        cursor.fetchone.side_effect = [[1], [2]]
        cursor.execute.side_effect = [
            None,  # SELECT do MAX
            Exception("ORA-00001: unique constraint (SAPIENS.CP_USU_TCONROM) violated"),
            None,  # SELECT do MAX de novo
            None,  # INSERT aceito
        ]
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNone(erro)
        self.assertEqual(entry.sequence_record, "3", "a segunda tentativa precisa usar o MAX novo")
        self.assertEqual(cursor.execute.call_count, 4)
        conexao.commit.assert_called_once()

    def test_erro_que_nao_e_colisao_falha_na_primeira(self):
        """Repetir um erro real só atrasaria a resposta para quem está no galpão."""
        entry = self._entry_para_oracle("6401")

        cursor = MagicMock()
        cursor.fetchone.return_value = [0]
        cursor.execute.side_effect = [
            None,  # SELECT do MAX
            Exception("ORA-12899: value too large for column"),
        ]
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNotNone(erro)
        self.assertIn("ORA-12899", erro)
        self.assertEqual(cursor.execute.call_count, 2)
        conexao.commit.assert_not_called()

    def test_colisao_insistente_desiste_e_reporta(self):
        """Se o número continua colidindo, a resposta precisa sair mesmo assim."""
        entry = self._entry_para_oracle("6402")

        cursor = MagicMock()
        cursor.fetchone.return_value = [5]
        cursor.execute.side_effect = [
            None,
            Exception("ORA-00001: unique constraint violated"),
            None,
            Exception("ORA-00001: unique constraint violated"),
            None,
            Exception("ORA-00001: unique constraint violated"),
        ]
        conexao = MagicMock()
        conexao.cursor.return_value = cursor

        with patch("hqbooking.views._connect_simulation_oracle", return_value=(conexao, "oracledb")):
            erro = _insert_simulation_romaneio_oracle(entry)

        self.assertIsNotNone(erro)
        self.assertIn("ORA-00001", erro)
        self.assertEqual(cursor.execute.call_count, 6, "três tentativas, não mais")
        conexao.commit.assert_not_called()

    # Payload real, copiado de uma etiqueta de produção em 03/09/2026. Fica
    # aqui porque um exemplo inventado não prova nada sobre o que a impressora
    # emite: o peso vem com PONTO decimal ("187.100" são 187,1 kg, não 187 mil),
    # e é justamente a leitura que um parser brasileiro erra com facilidade.
    PAYLOAD_REAL = "1/2/6/187.100/428595/16"

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_oracle_ok.__func__)
    def test_payload_real_de_producao_e_aceito(self, insert_mock):
        response = self._post({"barcode_payload": self.PAYLOAD_REAL, "record_type": 1})

        self.assertEqual(response.status_code, 200, msg=response.content.decode("utf-8"))
        entry = SimulationRomaneioEntry.objects.get()
        self.assertEqual(entry.company_code, "1")
        self.assertEqual(entry.branch_code, "2")
        self.assertEqual(entry.volume_quantity, 6)
        self.assertEqual(entry.package_code, "428595")
        self.assertEqual(entry.address_code, "16")
        self.assertEqual(entry.record_type, 1)

    def test_peso_com_ponto_decimal_nao_vira_milhar(self):
        """187.100 são 187,1 kg — seis volumes não pesam 187 toneladas.

        O ponto aqui é separador decimal, não de milhar. Se alguém "corrigir" o
        parser para tratar ponto como milhar, este teste cai — e cairia também a
        conferência de peso do galpão inteiro, com erro de mil vezes.
        """
        mapeado = _extract_romaneio_payload(self.PAYLOAD_REAL)
        self.assertEqual(mapeado["romaneio_weight"], Decimal("187.100"))
        self.assertLess(mapeado["romaneio_weight"], Decimal("1000"))

    def test_o_qr_entra_pelo_mesmo_caminho_do_codigo_de_barras(self):
        """Simbologia não muda nada: o servidor recebe texto, não imagem.

        O app manda `barcode_payload` venha de onde vier, então o que garante o
        QR funcionando é o payload ser o mesmo — e é isso que este teste fixa.
        """
        self.assertEqual(
            _extract_romaneio_payload(self.PAYLOAD_REAL),
            _extract_romaneio_payload(self.PAYLOAD_REAL.replace("/", "|")),
        )

    def test_estagios_do_app_batem_com_o_servidor(self):
        """`src/stages.ts` é uma cópia da lista daqui, e cópia sai de sincronia.

        O app decide o número que vai para USU_TIPREG a partir do arquivo dele.
        Se alguém renomear uma etapa só de um lado, os dois continuam
        funcionando — e o ERP passa a receber rótulo e número discordantes.
        """
        arquivo = (
            Path(__file__).resolve().parent.parent.parent
            / "connectmx-mobile"
            / "src"
            / "stages.ts"
        )
        if not arquivo.exists():
            self.skipTest("app Expo nao esta ao lado deste repositorio")

        encontrados = re.findall(
            r"\{\s*id:\s*(\d+),\s*label:\s*'([^']+)'",
            arquivo.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            [(int(numero), rotulo) for numero, rotulo in encontrados],
            list(SimulationRomaneioEntry.RECORD_TYPE_CHOICES),
        )
