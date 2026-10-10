"""Write the files the Go agent is built and tested from: the command catalogue, a large set of validation cases, and signed test envelopes. Run it after changing apps/agent/catalog.py."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.agent import golden, spec


def files():
    sign, public = spec.fixed_signer()
    return {"catalog.json": spec.build_spec(), "validation_cases.json": spec.build_cases(),
            "envelopes.json": {"public_key": public, "node": 7, "now": 1_800_000_000, "cases": spec.build_envelopes(sign)},
            "golden.json": golden.build_golden()}


def render(data):
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


class Command(BaseCommand):
    help = "Write agent/spec/*.json from the Python catalogue (the single source of truth)."

    def handle(self, *args, **options):
        folder = Path(settings.BASE_DIR) / "agent" / "spec"
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in files().items():
            (folder / name).write_text(render(data), encoding="utf-8", newline="\n")
            self.stdout.write(f"wrote {folder / name}")
