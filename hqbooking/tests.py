import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import SimulationRomaneioEntry, Tire, TireMovement, Truck, TruckModelTemplate, TruckTireChange


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class TruckTireFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="tester",
            password="secret123",
            userId="0001",
            email="tester@example.com",
        )
        self.client.force_login(self.user)

        self.template = TruckModelTemplate.objects.create(
            name="Modelo Teste",
            axle_count=1,
            wheel_count=4,
            structure_json=json.dumps(
                [
                    {
                        "left": [{"name": "DE"}],
                        "right": [{"name": "DD"}],
                        "spares": [{"name": "Estepe 1"}, {"name": "Estepe 2"}],
                    }
                ]
            ),
        )
        self.truck = Truck.objects.create(
            identifier="CAM-001",
            model_template=self.template,
            tire_count=self.template.wheel_count,
            layout_model="TEMPLATE",
        )

    def test_can_create_install_and_return_tire_to_stock(self):
        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "tire_update",
                "truck_id": self.truck.id,
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
                truck=self.truck,
                tire_number=1,
                tire=tire,
                tire_brand="Goodyear",
            ).exists()
        )
        self.assertTrue(
            TireMovement.objects.filter(tire=tire, movement_type=TireMovement.TYPE_INSTALL, truck=self.truck).exists()
        )

        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "tire_update",
                "truck_id": self.truck.id,
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
            TireMovement.objects.filter(tire=tire, movement_type=TireMovement.TYPE_TO_STOCK, truck=self.truck).exists()
        )

    def test_truck_tire_page_renders_initial_guide(self):
        response = self.client.get(reverse("truck_tire_control"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "truck-logistics-page")
        self.assertContains(response, "logistics-shell")
        self.assertContains(response, "Guia inicial")
        self.assertContains(response, "Passo a passo da opera")
        self.assertContains(response, "truck-guide-step-card")

    def test_truck_tire_page_renders_dashboard_tab(self):
        tire = Tire.objects.create(
            brand="Goodyear",
            serial_number="DASH-001",
            status=Tire.STATUS_STOCK,
        )
        TireMovement.objects.create(
            tire=tire,
            movement_type=TireMovement.TYPE_REGISTER,
            movement_date=timezone.localdate(),
            note="Entrada para dashboard",
        )

        response = self.client.get(
            reverse("truck_tire_control"),
            {"tab": "dashboard"},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard da log")
        self.assertContains(response, "truck-dashboard-grid")
        self.assertContains(response, "truck-dashboard-ring-grid")
        self.assertContains(response, "truck-dashboard-day-chart")

    def test_truck_tire_page_renders_model_creation_modal(self):
        response = self.client.get(
            reverse("truck_tire_control"),
            {"tab": "models"},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "logisticsToast")
        self.assertContains(response, "showLogisticsToast")
        self.assertContains(response, "truckModelCreateModal")
        self.assertContains(response, "openTruckModelCreateModal")
        self.assertContains(response, "Editar modelo selecionado")
        self.assertContains(response, "truck-model-modal-shell")

    def test_truck_tire_page_renders_movements_and_history_tabs(self):
        movement_response = self.client.get(
            reverse("truck_tire_control"),
            {"tab": "movements", "truck": self.truck.id},
            HTTP_HOST="localhost",
        )
        history_response = self.client.get(
            reverse("truck_tire_control"),
            {"tab": "history", "truck": self.truck.id},
            HTTP_HOST="localhost",
        )

        self.assertEqual(movement_response.status_code, 200)
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(movement_response, "Moviment")
        self.assertContains(history_response, "Hist")

    def test_inventory_tab_renders_modernized_tire_workflow(self):
        response = self.client.get(
            reverse("truck_tire_control"),
            {"tab": "inventory"},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "truck-inventory-summary-grid")
        self.assertContains(response, "truck-process-grid")
        self.assertContains(response, "truck-filter-chip-bar")
        self.assertContains(response, "inventoryBatchModeGrid")
        self.assertContains(response, "inventory_serial_batch")
        self.assertContains(response, "inventory_batch_prefix")
        self.assertContains(response, "inventoryBatchPreviewList")

    def test_can_create_tires_in_batch(self):
        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "create_tire",
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
            reverse("truck_tire_control"),
            {
                "form_id": "create_tire",
                "batch_mode": "generate",
                "brand": "Pirelli",
                "batch_prefix": "P-",
                "batch_start_number": "7",
                "batch_quantity": "4",
                "batch_pad_length": "3",
                "registered_on": "2026-07-10",
                "purchase_value": "1200,00",
                "note": "Lote sequencial",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Tire.objects.filter(serial_number__in=["P-007", "P-008", "P-009", "P-010"]).count(),
            4,
        )
        self.assertEqual(
            TireMovement.objects.filter(
                tire__serial_number__in=["P-007", "P-008", "P-009", "P-010"],
                movement_type=TireMovement.TYPE_REGISTER,
            ).count(),
            4,
        )

    def test_can_delete_stock_tire_permanently(self):
        tire = Tire.objects.create(
            brand="Michelin",
            serial_number="P-DELETE",
            status=Tire.STATUS_STOCK,
        )

        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "inventory_action",
                "tire_id": tire.id,
                "inventory_action": "delete_permanently",
            },
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
            reverse("truck_tire_control"),
            {
                "form_id": "inventory_action",
                "tire_id": tire.id,
                "inventory_action": "delete_permanently",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Tire.objects.filter(pk=tire.id).exists())

    def test_truck_tire_history_page_renders_logistics_motion_shell(self):
        response = self.client.get(reverse("truck_tire_history"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "truck-logistics-page")
        self.assertContains(response, "logistics-shell")
        self.assertContains(response, "Hist")

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
            brand="Michelin",
            serial_number="P-ESTOQUE",
            status=Tire.STATUS_STOCK,
        )

        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "tire_update",
                "truck_id": self.truck.id,
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

        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "inventory_action",
                "tire_id": tire.id,
                "inventory_action": "retread",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tire.refresh_from_db()
        self.assertEqual(tire.recap_count, 3)

        response = self.client.post(
            reverse("truck_tire_control"),
            {
                "form_id": "inventory_action",
                "tire_id": tire.id,
                "inventory_action": "retread",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
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
            reverse("truck_tire_control"),
            {
                "form_id": "tire_update",
                "truck_id": self.truck.id,
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
            reverse("truck_tire_control"),
            {
                "form_id": "inventory_action",
                "tire_id": tire.id,
                "inventory_action": "return_from_retread",
                "inventory_cost": "320,75",
            },
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
            reverse("truck_tire_control"),
            {
                "form_id": "tire_update",
                "truck_id": self.truck.id,
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
            reverse("truck_tire_control"),
            {
                "form_id": "swap_tires",
                "truck_id": self.truck.id,
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

        front_assignment = TruckTireChange.objects.get(truck=self.truck, tire_number=2)
        rear_assignment = TruckTireChange.objects.get(truck=self.truck, tire_number=1)
        self.assertEqual(front_assignment.tire_id, front_tire.id)
        self.assertEqual(rear_assignment.tire_id, rear_tire.id)

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
