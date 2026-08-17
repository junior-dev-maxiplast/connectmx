import json
from decimal import Decimal
from unittest.mock import patch

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
                "barcode_payload": "1|2|123|8|1540,250",
                "company_code": "1",
                "branch_code": "2",
                "user_code": "9001",
                "volume_quantity": "8",
                "romaneio_weight": "1540,250",
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
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SimulationRomaneioEntry.objects.count(), 1)
        entry = SimulationRomaneioEntry.objects.first()
        self.assertEqual(entry.sync_status, SimulationRomaneioEntry.SYNC_ERROR)
        self.assertEqual(entry.sync_message, "Falha Oracle")
        insert_mock.assert_called_once()

    @patch("hqbooking.views._insert_simulation_romaneio_oracle", side_effect=_mock_oracle_insert_success.__func__)
    def test_romaneio_quick_submit_creates_entry(self, insert_mock):
        response = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps(
                {
                    "barcode_payload": "1/2/123/8/1540,250",
                    "user_code": "9001",
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
        insert_mock.assert_called_once()

    def test_romaneio_quick_submit_requires_user(self):
        response = self.client.post(
            reverse("logistics_romaneio_quick_submit"),
            data=json.dumps(
                {
                    "barcode_payload": "1/2/123/8/1540,250",
                    "user_code": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["status"], "error")
