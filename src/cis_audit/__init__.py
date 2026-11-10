"""CIS Linux Audit: auditoría CIS de hardening Linux de solo lectura.

Preparación (línea base Excel, catálogo de los CIS Benchmark suministrados, mapeo y matrices de alcance), recolección
remota read-only, evaluación técnica CIS, capa de resultado final y reportes HTML/XLSX/JSON.
"""

from pathlib import Path

_VERSION = Path(__file__).resolve().parents[2] / "VERSION"
__version__ = _VERSION.read_text(encoding="utf-8").strip() if _VERSION.exists() else "1.0.0"
