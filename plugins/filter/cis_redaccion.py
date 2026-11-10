"""Filtro Ansible `cis_redactar`: redacta secretos en el controlador antes de persistir evidencia."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cis_audit.motor.redaccion import redactar  # noqa: E402


class FilterModule(object):
    def filters(self):
        return {"cis_redactar": lambda texto: redactar(texto or "")}
