"""Redacción de datos sensibles antes de persistir evidencia.

Se aplica en el controlador (filtro Ansible `cis_redactar` y al importar
evidencia). Es una segunda barrera: los comandos ya evitan imprimir secretos.
"""

import re

MARCA = "<REDACTADO>"

_PATRONES = [
    # Bloques de claves privadas
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MARCA),
    # Hash PBKDF2 de GRUB
    (re.compile(r"(grub\.pbkdf2\.[a-z0-9]+)\.[0-9]+\.[0-9A-Fa-f.]+"), r"\1." + MARCA),
    (re.compile(r"(password_pbkdf2\s+\S+\s+)(?!<REDACTADO>)\S+"), r"\1" + MARCA),
    # Hashes crypt ($1$, $5$, $6$, $y$, $2b$ ...)
    (re.compile(r"\$(1|2[abxy]?|5|6|y|gy|7)\$[^\s:]{8,}"), MARCA),
    # Credenciales en URL: esquema://usuario:clave@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1" + MARCA + "@"),
    # Parámetros de consulta sensibles
    (re.compile(r"([?&;](?:credentials|token|access_token|auth|password|passwd|pwd|secret|key|apikey|api_key)=)[^&\s\"']+", re.I),
     r"\1" + MARCA),
    # Asignaciones clave=valor sensibles (repos, rsyslog, conf)
    (re.compile(r"(\b(?:password|passwd|pwd|secret|token|proxy_password|apikey|api_key|sslclientkeypass)\s*[=:]\s*\"?)[^\s\"',)]+",
                re.I), r"\1" + MARCA),
]


def redactar(texto: str) -> str:
    if not texto:
        return texto
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto
