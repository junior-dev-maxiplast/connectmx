"""Publica, lista ou remove atualizações remotas do app ConnectMX Mobile.

No projeto do app: `npm run atualizacao` gera a pasta `atualizacao/` (o `expo
export` do Android mais o `expoConfig.json`). Copie a pasta para o servidor e:

    python manage.py publicar_app_mobile --pasta C:\\caminho\\atualizacao

Os celulares com a mesma versão do app recebem a atualização ao abrir o app (ou
pelo aviso "Nova versão do app pronta"). `--listar` mostra o que está publicado e
`--remover <data>` tira uma atualização: os aparelhos voltam para a anterior na
próxima consulta.
"""

import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from hqbooking import mobile_updates


def runtime_from_config(config):
    """A versão que recebe a atualização: a runtime do app (política appVersion = campo version)."""
    runtime = config.get("runtimeVersion")
    if isinstance(runtime, str) and runtime.strip():
        return runtime.strip()
    if isinstance(runtime, dict) and runtime.get("policy") == "appVersion" and config.get("version"):
        return str(config["version"]).strip()
    return None


class Command(BaseCommand):
    help = "Publica, lista ou remove atualizações remotas do app ConnectMX Mobile (expo-updates)."

    def add_arguments(self, parser):
        parser.add_argument("--pasta", help="Pasta gerada por `npm run atualizacao` no projeto do app.")
        parser.add_argument("--runtime", help="Versão do app que recebe a atualização (padrão: a do expoConfig.json).")
        parser.add_argument("--listar", action="store_true", help="Lista as atualizações publicadas.")
        parser.add_argument("--remover", help="Data (nome) da atualização a remover; use com --runtime.")

    def handle(self, *args, **options):
        if options.get("listar"):
            return self._list()
        if options.get("remover"):
            return self._remove(options.get("runtime"), options["remover"])
        if not options.get("pasta"):
            raise CommandError("Informe --pasta (ou use --listar / --remover).")
        return self._publish(Path(options["pasta"]), options.get("runtime"))

    def _publish(self, folder, runtime):
        if not (folder / "metadata.json").is_file():
            raise CommandError(f"{folder} não tem metadata.json: gere a pasta com `npm run atualizacao` no projeto do app.")
        try:
            bundle, assets = mobile_updates.platform_files(folder)
            config = mobile_updates.read_json(folder / "expoConfig.json") if (folder / "expoConfig.json").is_file() else {}
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        runtime = (runtime or runtime_from_config(config) or "").strip()
        if not runtime:
            raise CommandError("Não deu para saber a versão do app: falta o expoConfig.json; informe --runtime (ex.: 1.2.0).")
        if not mobile_updates.safe_name(runtime):
            raise CommandError(f"Versão inválida: {runtime}")

        name = timezone.now().strftime("%Y%m%d%H%M%S")
        target = mobile_updates.updates_root() / runtime / name
        if target.exists():
            raise CommandError(f"Já existe uma publicação com o nome {name}; tente de novo em um segundo.")
        new_id = mobile_updates.update_id(folder)
        latest = mobile_updates.latest_update_dir(runtime)
        if latest is not None and mobile_updates.update_id(latest) == new_id:
            raise CommandError(f"Esta pasta já é a atualização publicada ({latest.name}): nada mudou.")

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, target)
        self.stdout.write(self.style.SUCCESS(
            f"Publicada a atualização {name} para o app versão {runtime} (id {new_id}): "
            f"bundle {bundle} e {len(assets)} arquivo(s)."
        ))
        self.stdout.write("Os celulares baixam ao abrir o app; o aviso \"Nova versão do app pronta\" aplica na hora.")

    def _list(self):
        root = mobile_updates.updates_root()
        runtimes = sorted(item.name for item in root.iterdir() if item.is_dir()) if root.is_dir() else []
        if not runtimes:
            self.stdout.write("Nenhuma atualização publicada.")
            return
        for runtime in runtimes:
            updates = mobile_updates.published_updates(runtime)
            self.stdout.write(f"App versão {runtime}: {len(updates)} atualização(ões)")
            for index, update in enumerate(updates):
                marker = "  (atual)" if index == len(updates) - 1 else ""
                self.stdout.write(f"  {update.name}  id {mobile_updates.update_id(update)}{marker}")

    def _remove(self, runtime, name):
        if not runtime:
            raise CommandError("Informe --runtime junto com --remover (veja os nomes com --listar).")
        if not (mobile_updates.safe_name(runtime) and mobile_updates.safe_name(name)):
            raise CommandError("Versão ou nome inválido.")
        target = mobile_updates.updates_root() / runtime / name
        if not target.is_dir():
            raise CommandError(f"Atualização {name} não encontrada para a versão {runtime}.")
        shutil.rmtree(target)
        latest = mobile_updates.latest_update_dir(runtime)
        self.stdout.write(self.style.SUCCESS(
            f"Removida a atualização {name}. "
            + (f"Os celulares voltam para {latest.name} na próxima consulta." if latest else "Não sobrou atualização publicada para esta versão.")
        ))
