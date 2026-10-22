"""Quem pode baixar cada arquivo enviado (/media/)."""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .models import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeEntryAttachment,
    PortalChatUpload,
    PortalDemandAttachment,
)
from .tests_portal_workflow import PortalWorkflowTestCase

_MEDIA_ROOT = tempfile.mkdtemp(prefix="connectmx-media-tests-")


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ProtectedMediaTests(PortalWorkflowTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.demand = self._open_demand()
        self.attachment = PortalDemandAttachment.objects.create(
            demand=self.demand,
            original_name="print.png",
            file=SimpleUploadedFile("print.png", b"\x89PNG fake", content_type="image/png"),
        )
        self.url = self.attachment.file.url
        self.stranger = get_user_model().objects.create_user(
            username="outro.solicitante", password="pw", userId="99001", email="outro@example.com"
        )

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_the_requester_downloads_their_own_attachment(self):
        self.client.force_login(self.requester)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"\x89PNG fake")

    def test_the_attendant_downloads_any_attachment(self):
        self.client.force_login(self.attendant)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_another_requester_gets_404(self):
        self.client.force_login(self.stranger)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_file_reused_by_the_knowledge_base_opens_for_any_user(self):
        category = KnowledgeCategory.objects.create(name="Infra")
        entry = KnowledgeEntry.objects.create(
            category=category, title="Coletor", trigger="Sem rede", description="Sem rede",
            resolution="Reiniciar", created_by=self.attendant,
        )
        KnowledgeEntryAttachment.objects.create(entry=entry, file=self.attachment.file.name)
        self.client.force_login(self.stranger)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_chat_upload_is_private_to_its_owner(self):
        upload = PortalChatUpload.objects.create(
            user=self.requester,
            original_name="log.txt",
            file=SimpleUploadedFile("log.txt", b"erro", content_type="text/plain"),
        )

        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(upload.file.url).status_code, 404)
        self.client.force_login(self.requester)
        self.assertEqual(self.client.get(upload.file.url).status_code, 200)

    def test_html_attachment_is_forced_to_download(self):
        html = PortalDemandAttachment.objects.create(
            demand=self.demand,
            original_name="pagina.html",
            file=SimpleUploadedFile("pagina.html", b"<script>alert(1)</script>", content_type="text/html"),
        )
        self.client.force_login(self.requester)

        response = self.client.get(html.file.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], "attachment")
        self.assertEqual(response["Content-Security-Policy"], "sandbox")

    def test_tire_photos_open_without_login_like_the_tire_screens(self):
        import os

        os.makedirs(os.path.join(_MEDIA_ROOT, "tires", "movements"), exist_ok=True)
        with open(os.path.join(_MEDIA_ROOT, "tires", "movements", "foto.jpg"), "wb") as handle:
            handle.write(b"jpeg")

        response = self.client.get("/media/tires/movements/foto.jpg")

        self.assertEqual(response.status_code, 200)

    def test_unknown_file_and_path_traversal_get_404(self):
        self.client.force_login(self.attendant)

        self.assertEqual(self.client.get("/media/portal_demands/nao-existe.png").status_code, 404)
        self.assertEqual(self.client.get("/media/../connect/settings.py").status_code, 404)
        self.assertEqual(self.client.get("/media/%2E%2E/connect/settings.py").status_code, 404)
