"""Motor de auditoría: recolección read-only, evaluación y resultados."""

# El catálogo de controles registra también los de la Parte 1 (controles_p1 importa de controles):
# cargarlo primero hace determinista el orden de importación.
from . import controles  # noqa: E402,F401
