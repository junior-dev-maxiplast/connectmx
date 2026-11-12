"""Atualização remota do app: publicação, manifesto (protocolo expo-updates 1) e arquivos."""

import base64
import hashlib
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import mobile_updates
from .management.commands.publicar_app_mobile import runtime_from_config

PUBLIC_URL = "http://connectmx.maxiplast.com.br:8888"
BUNDLE = b"var app = 'ConnectMX'; // bundle de teste"
IMAGE = b"\x89PNG\r\n\x1a\nimagem de teste"
HEADERS = {
    "HTTP_EXPO_PLATFORM": "android",
    "HTTP_EXPO_RUNTIME_VERSION": "1.2.0",
    "HTTP_EXPO_PROTOCOL_VERSION": "1",
}


def make_export(folder, bundle=BUNDLE, version="1.2.0"):
    """Pasta como a do `npm run atualizacao`: metadata.json, bundle, imagem e expoConfig.json."""
    folder = Path(folder)
    (folder / "_expo/static/js/android").mkdir(parents=True)
    (folder / "assets").mkdir()
    # Como no `expo export`: o nome do bundle leva o hash do conteúdo.
    bundle_path = f"_expo/static/js/android/index-{hashlib.md5(bundle).hexdigest()[:12]}.hbc"
    (folder / bundle_path).write_bytes(bundle)
    (folder / "assets/9f8e7d").write_bytes(IMAGE)
    metadata = {
        "version": 0,
        "bundler": "metro",
        "fileMetadata": {"android": {"bundle": bundle_path, "assets": [{"path": "assets/9f8e7d", "ext": "png"}]}},
    }
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    config = {"name": "ConnectMX", "version": version, "runtimeVersion": {"policy": "appVersion"}}
    # Como o `>` do PowerShell 5.1 grava: UTF-16.
    (folder / "expoConfig.json").write_bytes(json.dumps(config).encode("utf-16"))
    return folder


def parts_of(response):
    """Partes do multipart pelo nome do Content-Disposition, como o cliente lê."""
    boundary = response["Content-Type"].split("boundary=")[1]
    parts = {}
    for chunk in response.content.decode("utf-8").split(f"--{boundary}"):
        if "Content-Disposition" not in chunk:
            continue
        head, body = chunk.split("\r\n\r\n", 1)
        name = head.split('name="')[1].split('"')[0]
        parts[name] = json.loads(body.rstrip("\r\n"))
    return parts


class MobileUpdatesTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "app_updates"
        overrides = override_settings(MOBILE_APP_UPDATES_DIR=str(self.root), CONNECTMX_PUBLIC_URL=PUBLIC_URL)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.url = reverse("mobile_update_manifest")

    def publish(self, name="pacote", **kwargs):
        folder = make_export(Path(self.tmp.name) / name, **kwargs)
        call_command("publicar_app_mobile", pasta=str(folder), stdout=StringIO())
        return folder

    def test_without_update_answers_204(self):
        response = self.client.get(self.url, **HEADERS)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["expo-protocol-version"], "1")

    def test_manifest_follows_the_protocol(self):
        self.publish()
        response = self.client.get(self.url, **HEADERS)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("multipart/mixed; boundary="))
        self.assertEqual(response["expo-protocol-version"], "1")
        self.assertEqual(response["expo-sfv-version"], "0")
        parts = parts_of(response)
        manifest = parts["manifest"]
        self.assertEqual(parts["extensions"], {"assetRequestHeaders": {}})
        self.assertEqual(manifest["runtimeVersion"], "1.2.0")
        self.assertRegex(manifest["createdAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
        self.assertEqual(manifest["extra"]["expoClient"]["name"], "ConnectMX")
        launch = manifest["launchAsset"]
        expected_hash = base64.urlsafe_b64encode(hashlib.sha256(BUNDLE).digest()).decode().rstrip("=")
        self.assertEqual(launch["hash"], expected_hash)
        self.assertEqual(launch["key"], hashlib.md5(BUNDLE).hexdigest())
        self.assertEqual((launch["fileExtension"], launch["contentType"]), (".bundle", "application/javascript"))
        self.assertTrue(launch["url"].startswith(PUBLIC_URL + "/api/mobile/atualizacoes/arquivos/1.2.0/"))
        image = manifest["assets"][0]
        self.assertEqual((image["fileExtension"], image["contentType"]), (".png", "image/png"))

        # O endereço do manifesto baixa exatamente os bytes que o hash promete.
        bundle = self.client.get(launch["url"][len(PUBLIC_URL):])
        self.assertEqual(b"".join(bundle.streaming_content), BUNDLE)
        self.assertEqual(bundle["Content-Type"], "application/javascript")
        picture = self.client.get(image["url"][len(PUBLIC_URL):])
        self.assertEqual(b"".join(picture.streaming_content), IMAGE)

    def test_same_update_on_the_phone_answers_204(self):
        self.publish()
        manifest = parts_of(self.client.get(self.url, **HEADERS))["manifest"]
        again = self.client.get(self.url, HTTP_EXPO_CURRENT_UPDATE_ID=manifest["id"].upper(), **HEADERS)
        self.assertEqual(again.status_code, 204)

    def test_only_the_same_app_version_gets_the_update(self):
        self.publish()
        other = {**HEADERS, "HTTP_EXPO_RUNTIME_VERSION": "1.1.0"}
        self.assertEqual(self.client.get(self.url, **other).status_code, 204)
        self.assertEqual(self.client.get(self.url, HTTP_EXPO_RUNTIME_VERSION="1.2.0").status_code, 400)
        self.assertEqual(self.client.get(self.url, **{**HEADERS, "HTTP_EXPO_RUNTIME_VERSION": "../x"}).status_code, 400)

    def test_files_never_leave_the_update_folder(self):
        self.publish()
        update = mobile_updates.latest_update_dir("1.2.0").name
        (Path(self.tmp.name) / "segredo.txt").write_text("não", encoding="utf-8")
        for path in ("../../../segredo.txt", "..%2F..%2F..%2Fsegredo.txt", "nao-existe.png"):
            response = self.client.get(f"/api/mobile/atualizacoes/arquivos/1.2.0/{update}/{path}")
            self.assertEqual(response.status_code, 404, path)

    def test_newest_publication_wins_and_removal_rolls_back(self):
        self.publish("primeiro")
        first = parts_of(self.client.get(self.url, **HEADERS))["manifest"]["id"]
        with self.assertRaises(CommandError):
            # Mesmo pacote de novo: nada mudou.
            call_command("publicar_app_mobile", pasta=str(Path(self.tmp.name) / "primeiro"), stdout=StringIO())
        mobile_updates_dir = self.root / "1.2.0"
        older = mobile_updates.latest_update_dir("1.2.0")
        older.rename(mobile_updates_dir / "20200101000000")  # garante que a próxima é a mais nova
        self.publish("segundo", bundle=b"var app = 'versao nova';")
        second = parts_of(self.client.get(self.url, **HEADERS))["manifest"]["id"]
        self.assertNotEqual(first, second)

        listing = StringIO()
        call_command("publicar_app_mobile", listar=True, stdout=listing)
        self.assertIn("(atual)", listing.getvalue())
        newest = mobile_updates.latest_update_dir("1.2.0").name
        call_command("publicar_app_mobile", runtime="1.2.0", remover=newest, stdout=StringIO())
        self.assertEqual(parts_of(self.client.get(self.url, **HEADERS))["manifest"]["id"], first)

    def test_runtime_comes_from_the_app_config(self):
        self.assertEqual(runtime_from_config({"version": "1.2.0", "runtimeVersion": {"policy": "appVersion"}}), "1.2.0")
        self.assertEqual(runtime_from_config({"runtimeVersion": "2.0.0"}), "2.0.0")
        self.assertIsNone(runtime_from_config({"runtimeVersion": {"policy": "fingerprint"}}))
        with self.assertRaises(CommandError):
            call_command("publicar_app_mobile", pasta=str(Path(self.tmp.name) / "nao-existe"), stdout=StringIO())
