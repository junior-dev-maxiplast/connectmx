from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import userQueue


class ReorderQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            userId="10001",
            username="test.user",
            email="test.user@example.com",
            nameUser="Test User",
            password="pw",
        )
        self.client.login(username="test.user", password="pw")

        # Positions 2..4 are reorderable (position 1 reserved for current item).
        self.i2 = userQueue.objects.create(user_code="10001", n_queue_position=2, a_description="a")
        self.i3 = userQueue.objects.create(user_code="10001", n_queue_position=3, a_description="b")
        self.i4 = userQueue.objects.create(user_code="10001", n_queue_position=4, a_description="c")

    def test_reorder_updates_positions(self):
        url = reverse("reorderQueueItems")
        payload = {"order": [self.i4.n_register, self.i2.n_register, self.i3.n_register]}
        resp = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        self.i4.refresh_from_db()
        self.i2.refresh_from_db()
        self.i3.refresh_from_db()

        self.assertEqual(self.i4.n_queue_position, 2)
        self.assertEqual(self.i2.n_queue_position, 3)
        self.assertEqual(self.i3.n_queue_position, 4)

