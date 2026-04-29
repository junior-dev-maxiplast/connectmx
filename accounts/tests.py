from django.test import TestCase
from django.contrib.auth import get_user_model


class UserModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            userId="10001",
            username="test.user",
            email="test.user@example.com",
            nameUser="Test User",
            password="change-me-in-real-tests",
        )

    def test_can_authenticate_with_userid(self):
        self.assertTrue(self.user.check_password("change-me-in-real-tests"))

