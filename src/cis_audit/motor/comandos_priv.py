"""Grupos de evidencia exclusivos del recolector principal privilegiado (cis-audit-collect).

Solo se ejecutan dentro del recolector privilegiado firmado (nunca en el recolector sin sudo: solo_privilegiado).
Principio: leer internamente lo necesario y devolver únicamente información normalizada mínima.
- priv.ssh_efectiva: `sshd -t` (rc) + `sshd -T` filtrado a una lista blanca de claves CIS; allow/deny solo como
  cantidad; Match detectado internamente (tipos de criterio y claves CIS redefinidas, nunca los valores del criterio).
  OL8: OPTIONS/CRYPTO_POLICY por lectura ESTÁTICA (nunca `source`); un token no literal bloquea.
- priv.sudo_politica: las búsquedas del Audit CIS sobre /etc/sudoers* evaluadas con awk; nunca se emite una línea
  de sudoers. Instrumentación: SHA-256 de los bytes exactos de /etc/sudoers.d/cis_audit (identidad) y del contenido
  efectivo normalizado (comprobación adicional), tipo y modo/propietario; nunca su texto.
  Valor por defecto de timestamp_timeout: solo la línea correspondiente de `/usr/bin/sudo -V` (LC_ALL=C).
- priv.cuentas: el campo de contraseña de /etc/shadow se clasifica dentro de awk (hash/bloqueada/vacía) y nunca
  se imprime; campos 3-8; INACTIVE de /etc/default/useradd; fecha del host en días.

- priv.kmod_config, priv.motd y priv.pwquality: directivas install/blacklist de 6 módulos, indicadores de información del
  sistema en /etc/motd (nunca el texto) y claves de complejidad de pwquality.
- Grupos añadidos al final del script en la versión 1.4.0 (las líneas anteriores no cambian):
- priv.rsyslog_validacion: `rsyslogd -N1` (modo verificación de sintaxis; no arranca el demonio) -> rc y mensajes.
- priv.logrotate: líneas sin comentarios de /etc/logrotate.conf y /etc/logrotate.d/* + `logrotate -d` (modo depuración:
  no rota, no actualiza el estado ni ejecuta scripts) filtrado a patrones, archivos considerados y errores.
- priv.journal_archivos: tipo, modo, propietario y grupo de /var/log/journal y /run/log/journal (profundidad 2).
- priv.unconfined_detalle: PID, comm, contexto, ejecutable (readlink /proc/PID/exe) y paquete RPM de los procesos
  unconfined_service_t.
- priv.suid_rpm: archivos SUID/SGID registrados en la base RPM y `rpm -V --noscripts --nodeps` de sus paquetes, solo
  las líneas de esos archivos (nunca ejecuta %verifyscript).
- priv.root_path_dirs: readlink -e y stat -L (modo, propietario, tipo del DESTINO) de los directorios candidatos del
  PATH de root; nunca ejecuta el perfil de root (R6).
- Grupo añadido al final en la versión 1.5.0:
- priv.authorized_keys: llaves autorizadas de las cuentas de automatización (PRE-3): AuthorizedKeysFile efectivo,
  metadatos de cada archivo y de su directorio, y por llave solo bits, fingerprint SHA256 y tipo (ssh-keygen -l).
- Grupos añadidos al final en la versión 1.6.0 (mismo texto e id que en el recolector sin sudo, ejecutados como root):
- rsyslog.conf: configuración de rsyslog sin comentarios (los archivos de /etc/rsyslog.d pueden ser solo de root en SLES).
- p1.kmod: módulos de kernel de la Parte 1, incluidos sctp y dccp (directivas de modprobe.d legibles solo por root).
- Versión 1.7.0 (sin grupos nuevos): usr.root_path emite también las líneas return/exit/exec/logout y la marca FLUJO|1.
"""

from .comandos import AMBAS, RHEL, Comando
from .comandos_p1 import SYSCTL_P1, _RE_SYSCTL, _legibles

SSHD_CLAVES = ("loglevel", "disableforwarding", "maxauthtries", "ignorerhosts", "hostbasedauthentication", "permitrootlogin",
               "permitemptypasswords", "permituserenvironment", "clientaliveinterval", "clientalivecountmax", "logingracetime",
               "banner", "usepam", "maxstartups", "maxsessions")
SSHD_ACCESO = ("allowusers", "allowgroups", "denyusers", "denygroups")
_K = "|".join(SSHD_CLAVES + SSHD_ACCESO)

# --- SSH ------------------------------------------------------------------------------------------------------
_SSH_INICIO = (
    "S=/usr/sbin/sshd; [ -x \"$S\" ] || S=/usr/bin/sshd; "
    "if [ ! -x \"$S\" ]; then echo 'SSHD|NO_INSTALADO'; exit 0; fi; "
    "C=/etc/ssh/sshd_config; [ -e \"$C\" ] || C=/usr/etc/ssh/sshd_config; echo \"CONFIG|$C\"; "
    "\"$S\" -t; echo \"SSHD_SINTAXIS|$?\"; A=(); "
)
# OL/RHEL 8 (Audit v4: sshd -T $OPTIONS $CRYPTO_POLICY): lectura estática, solo tokens literales permitidos.
_SSH_OPCIONES_OL8 = (
    "__val() { [ -r \"$1\" ] || return 0; awk -v K=\"$2\" '$0 ~ \"^[ \\t]*\" K \"=\" {v=substr($0,index($0,\"=\")+1)} END{print v}' \"$1\"; }; "
    "O=\"$(__val /etc/sysconfig/sshd OPTIONS) $(__val /etc/crypto-policies/back-ends/opensshserver.config CRYPTO_POLICY)\"; "
    "O=\"${O//\\\"/ }\"; O=\"${O//\\'/ }\"; b=0; set -f; for t in $O; do case \"$t\" in "
    "*'$'*|*'`'*|*';'*|*'&'*|*'|'*|*'<'*|*'>'*|*'('*|*')'*|*'\\\\'*) b=1;; -o[A-Za-z]*=*|-u[0-9]*|-4|-6) A+=(\"$t\");; *) b=1;; esac; done; set +f; "
    "if [ \"$b\" = 1 ]; then echo 'OPCIONES_OL8|bloqueado|construccion_no_literal'; A=(); else echo \"OPCIONES_OL8|ok|n=${#A[@]}\"; fi; "
)
_SSH_FIN = (
    "o=\"$(\"$S\" -T \"${A[@]}\")\"; echo \"SSHD_T_RC|$?\"; "
    "printf '%s\\n' \"$o\" | awk '$1 ~ /^(" + "|".join(SSHD_CLAVES) + ")$/ {print \"K|\" $1 \"|\" substr($0, length($1)+2)} "
    "$1 ~ /^(" + "|".join(SSHD_ACCESO) + ")$/ {n[$1]+=NF-1} "
    "END{split(\"" + " ".join(SSHD_ACCESO) + "\",a,\" \"); for(i=1;i<=4;i++) print \"ACCESO|\" a[i] \"|\" (n[a[i]]+0)}'; "
    # Include resueltos como sshd (relativos a /etc/ssh, comodines), solo para localizar bloques Match.
    "Q=(\"$C\"); V=' '; i=0; while [ \"$i\" -lt \"${#Q[@]}\" ] && [ \"$i\" -lt 256 ]; do f=\"${Q[$i]}\"; i=$((i+1)); "
    "case \"$V\" in *\" $f \"*) continue;; esac; V=\"$V$f \"; [ -r \"$f\" ] || continue; "
    "while IFS= read -r p; do case \"$p\" in /*) ;; *) p=\"/etc/ssh/$p\";; esac; for g in $p; do [ -f \"$g\" ] && Q+=(\"$g\"); done; "
    "done < <(awk 'tolower($1)==\"include\"{for(j=2;j<=NF;j++) print $j}' \"$f\"); done; "
    "set -f; L=($V); set +f; echo \"INCLUDE|$(( ${#L[@]} - 1 ))\"; "
    "awk -v K='^(" + _K + ")$' 'FNR==1{b=0} /^[ \\t]*#/{next} {k=tolower($1)} "
    "k==\"match\"{b=1; n++; c=\"\"; for(j=2;j<=NF;j+=2) c=c (c==\"\"?\"\":\",\") $j; B[n]=FILENAME \"|\" c; next} "
    "b && k ~ K {R[n]=R[n] (R[n]==\"\"?\"\":\";\") k \"=\" ((k ~ /^(allow|deny)/) ? \"#\" (NF-1) : $2)} "
    "END{print \"MATCH|\" (n+0); for(x=1;x<=n;x++) print \"MATCH_BLOQUE|\" x \"|\" B[x] \"|\" R[x]}' \"${L[@]}\""
)
SSH_EFECTIVA = _SSH_INICIO + _SSH_FIN
SSH_EFECTIVA_OL8 = _SSH_INICIO + _SSH_OPCIONES_OL8 + _SSH_FIN

# --- sudoers ---------------------------------------------------------------------------------------------------
INSTRUMENTACION = "/etc/sudoers.d/cis_audit"
# Normalización del contenido efectivo (sin comentarios ni líneas vacías, espacios colapsados): la MISMA en el
# script (awk) y en el controlador (privilegiado.normalizar_sudoers) para comparar SHA-256 sin emitir el texto.
_AWK_NORMALIZA = "{sub(/^[ \\t]+/,\"\"); sub(/[ \\t]+$/,\"\"); if ($0==\"\" || $0 ~ /^#/) next; gsub(/[ \\t]+/,\" \"); print}"
SUDO_POLITICA = (
    "I=0; grep -qE '^[#@]includedir[[:space:]]+/etc/sudoers\\.d([[:space:]]|$)' /etc/sudoers && I=1; "
    "for f in /etc/sudoers /etc/sudoers.d/* /etc/sudoers.*; do [ -f \"$f\" ] || continue; c=0; [ \"$f\" = /etc/sudoers ] && c=1; "
    "case \"$f\" in /etc/sudoers.d/*) b=\"${f##*/}\"; case \"$b\" in *.*|*~) ;; *) c=$I;; esac;; esac; "
    "echo \"ARCHIVO|$f|cargado=$c\"; awk -v F=\"$f\" -v C=\"$c\" '{l=$0; sub(/^[ \\t]+/,\"\",l)} l ~ /^#/ {next} "
    "l ~ /^Defaults/ {if (l ~ /^Defaults[ \\t]+([^#]+,[ \\t]*)?use_pty([ \\t,]|$)/) u++; "
    "if (l ~ /^Defaults[ \\t]+([^#]+,[ \\t]*)?![ \\t]*use_pty([ \\t,]|$)/) nu++; "
    "if (match(l,/timestamp_timeout[ \\t]*=[ \\t]*-?[0-9.]+/)) {v=substr(l,RSTART,RLENGTH); sub(/^[^=]*=[ \\t]*/,\"\",v); "
    "print \"TIMEOUT|\" v \"|\" ((l ~ /^Defaults[ \\t]/) ? \"global\" : \"con_alcance\") \"|\" F \"|\" FNR \"|cargado=\" C}} "
    "l ~ /NOPASSWD/ {split(l,a,/[ \\t]+/); print \"NOPASSWD|\" F \"|\" FNR \"|\" a[1] \"|cargado=\" C} "
    "l ~ /!authenticate/ {split(l,a,/[ \\t]+/); print \"NOAUTH|\" F \"|\" FNR \"|\" a[1] \"|cargado=\" C} "
    "END{print \"USE_PTY|\" F \"|presente=\" (u+0) \"|negado=\" (nu+0) \"|cargado=\" C}' \"$f\"; done; "
    # Identidad de la instrumentación: SHA-256 de los bytes exactos (sha_archivo, principal) + SHA semántico
    # (adicional); tipo sin seguir enlaces (un symlink nunca es la instrumentación).
    "f=" + INSTRUMENTACION + "; if [ -e \"$f\" ] || [ -L \"$f\" ]; then echo \"INSTR|existe=1|$(stat -c '%a|%u|%g' -- \"$f\")|"
    "tipo=$(stat -c '%F' -- \"$f\" | tr ' ' '_')|sha=$(awk '" + _AWK_NORMALIZA + "' \"$f\" | sha256sum | cut -d' ' -f1)|"
    "sha_archivo=$(sha256sum < \"$f\" | cut -d' ' -f1)\"; else echo 'INSTR|existe=0'; fi; "
    "if [ -x /usr/bin/sudo ]; then d=\"$(LC_ALL=C /usr/bin/sudo -V | awk -F': ' '/^Authentication timestamp timeout:/{print $2; exit}')\"; "
    "else d=''; fi; echo \"TIMEOUT_DEFAULT|${d:-NO_DETERMINADO}\"; "
    "visudo -c -q; echo \"VISUDO|$?\""
)

# --- cuentas ---------------------------------------------------------------------------------------------------
CUENTAS = (
    "awk -F: '{p=$2; h=(p ~ /^[$].+[$]/)?1:0; b=(p ~ /^[!*]/)?1:0; v=(p==\"\")?1:0; "
    "printf \"U|%s|hash=%d|bloqueada=%d|vacia=%d|%s|%s|%s|%s|%s|%s\\n\", $1, h, b, v, $3, $4, $5, $6, $7, $8}' /etc/shadow; "
    "if [ -r /etc/default/useradd ]; then i=\"$(awk -F= '/^[ \\t]*INACTIVE[ \\t]*=/{gsub(/[ \\t]/,\"\",$2); v=$2} END{print v}' /etc/default/useradd)\"; "
    "echo \"INACTIVE_DEFAULT|${i:-NO_DEFINIDO}|/etc/default/useradd\"; else echo 'INACTIVE_DEFAULT|NO_DEFINIDO|NO_EXISTE'; fi; "
    "echo \"HOY|$(( $(date +%s) / 86400 ))\""
)

# --- reutilización privilegiada de comandos de la Parte 1 (mismo id y formato) ----------------------------------
# sysctl: solo encabezados de archivos que contienen alguna de las 28 claves CIS.
SYSCTL_ARCHIVOS_ROOT = (
    _legibles("/etc/sysctl.conf /etc/sysctl.d/* /run/sysctl.d/* /usr/local/lib/sysctl.d/* /usr/lib/sysctl.d/* /lib/sysctl.d/*")
    + "C=/usr/lib/systemd/systemd-sysctl; [ -x \"$C\" ] || C=/lib/systemd/systemd-sysctl; "
    "\"$C\" --cat-config | awk -v R='" + _RE_SYSCTL + "' '/^[ \\t]*#[ \\t]*\\// {h=$0; next} $0 ~ R {if (h != \"\") {print h; h=\"\"} print}'; "
    "if [ -f /etc/sysctl.conf ] && [ \"$(readlink -f /etc/sysctl.d/99-sysctl.conf)\" != /etc/sysctl.conf ]; then "
    "grep -qE '" + _RE_SYSCTL + "' /etc/sysctl.conf && { echo '# /etc/sysctl.conf'; grep -E '" + _RE_SYSCTL + "' /etc/sysctl.conf; }; fi"
)
# Permisos SSH como root: mismo formato S|ruta|modo|usuario|grupo|tipo + acl_escritura (entradas ACL nominales con w).
SSH_PERMISOS_ROOT = (
    "__acl() { if command -v getfacl >/dev/null; then getfacl -cpn -- \"$1\" | awk -F: '($1==\"user\"||$1==\"group\") && $2!=\"\" && $3 ~ /w/' | wc -l; "
    "else echo NO_DISPONIBLE; fi; }; "
    "for f in /etc/ssh/sshd_config /etc/ssh/sshd_config.d; do if [ -e \"$f\" ]; then echo \"S|$(stat -Lc '%n|%#a|%U|%G|%F' -- \"$f\")|acl_escritura=$(__acl \"$f\")\"; "
    "else echo \"S|$f|NO_EXISTE\"; fi; done; "
    "for f in /etc/ssh/sshd_config.d/*.conf /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub; do "
    "[ -e \"$f\" ] && echo \"S|$(stat -Lc '%n|%#a|%U|%G|%F' -- \"$f\")|acl_escritura=$(__acl \"$f\")\"; done; echo 'S_FIN'"
)

# --- 1.3.0: módulos, MOTD y pwquality (Parte 1) -------------------------------------------------
# Lista CERRADA de módulos consumidores (1.1.1.1, 1.1.1.6, 1.1.1.7, 1.1.1.8, 3.2.2, 3.2.3). Nombre normalizado ('-' -> '_').
MODULOS_KMOD_1_3 = ("cramfs", "squashfs", "udf", "usb_storage", "tipc", "rds")
# Precedencia de kmod (de mayor a menor); un archivo con el mismo nombre en un directorio anterior oculta a los siguientes.
DIRS_MODPROBE = ("/etc/modprobe.d", "/run/modprobe.d", "/usr/local/lib/modprobe.d", "/usr/lib/modprobe.d", "/lib/modprobe.d")
# awk: une continuaciones ('\' final), ignora comentarios ('#' como primer carácter no blanco) y líneas vacías, y SOLO
# emite install/blacklist de los 6 módulos. El destino de install se reduce a true|false|OTRO (nunca su texto).
_AWK_KMOD = (
    "{ l = (c != \"\") ? c \" \" $0 : $0; c = \"\" } "
    "l ~ /\\\\$/ { sub(/\\\\$/, \"\", l); c = l; next } "
    "{ s = l; sub(/^[ \\t]+/, \"\", s) } "
    "s ~ /^#/ || s == \"\" { next } "
    "{ split(s, a, /[ \\t]+/); m = a[2]; gsub(/-/, \"_\", m) } "
    "(a[1] == \"install\" || a[1] == \"blacklist\") && m ~ M { t = \"\"; "
    "if (a[1] == \"install\") t = (a[3] ~ /^(\\/usr)?\\/bin\\/false$/) ? \"false\" : (a[3] ~ /^(\\/usr)?\\/bin\\/true$/) ? \"true\" : \"OTRO\"; "
    "print \"KC|\" F \"|\" FNR \"|\" a[1] \"|\" m \"|\" t } "
    "END { if (c != \"\") print \"KE|\" F \"|continuacion_incompleta\" }"
)
KMOD_CONFIG = (
    "for d in " + " ".join(DIRS_MODPROBE) + "; do for f in \"$d\"/*.conf; do [ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
    "if [ -L \"$f\" ] && [ \"$(readlink -- \"$f\")\" = /dev/null ]; then echo \"KF|$f|enmascarado\"; continue; fi; "
    "if [ ! -f \"$f\" ] || [ ! -r \"$f\" ]; then echo \"KE|$f|tipo_no_soportado\"; continue; fi; "
    "echo \"KF|$f|regular\"; awk -v F=\"$f\" -v M='^(" + "|".join(MODULOS_KMOD_1_3) + ")$' '" + _AWK_KMOD + "' \"$f\" "
    "|| echo \"KE|$f|error_lectura\"; done; done; echo 'K_FIN'"
)

# MOTD (solo 1.7.1). Sigue enlaces (cockpit), pero solo lee un destino final REGULAR (nunca FIFO, socket, dispositivo
# ni directorio), hasta MOTD_MAX_BYTES y con timeout de lectura. Nunca emite el texto: solo indicadores 0/1.
MOTD_MAX_BYTES = 65536
_MO_CERO = "|esc_v=0|esc_r=0|esc_m=0|esc_s=0|id_so=0"
MOTD = (
    "I=\"$(awk -F= '$1==\"ID\"{gsub(/\"/,\"\",$2); print $2}' /etc/os-release)\"; "
    "for f in /etc/motd /etc/motd.d/*; do [ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
    "o=\"$(stat -c '%F' -- \"$f\" | tr ' ' '_')\"; tp=\"$o\"; t=\"$f\"; "
    "if [ -L \"$f\" ]; then t=\"$(readlink -e -- \"$f\")\" || t=''; "
    "if [ -z \"$t\" ]; then echo \"MO|$f|tipo=$o:roto|legible=0|bytes=0" + _MO_CERO + "\"; continue; fi; "
    "tp=\"$o:$(stat -c '%F' -- \"$t\" | tr ' ' '_')\"; fi; "
    "if [ ! -f \"$t\" ] || [ -p \"$t\" ] || [ -S \"$t\" ] || [ -b \"$t\" ] || [ -c \"$t\" ] || [ -d \"$t\" ]; then "
    "echo \"MO|$f|tipo=$tp|legible=0|bytes=0" + _MO_CERO + "\"; continue; fi; "
    "b=\"$(stat -c '%s' -- \"$t\")\"; if [ \"$b\" -gt " + str(MOTD_MAX_BYTES) + " ]; then echo \"MO|$f|tipo=$tp|legible=0|bytes=$b" + _MO_CERO + "\"; continue; fi; "
    "c=\"$(timeout 5 head -c " + str(MOTD_MAX_BYTES) + " -- \"$t\")\" || { echo \"MO|$f|tipo=$tp|legible=0|bytes=$b" + _MO_CERO + "\"; continue; }; "
    "v=0; r=0; m=0; s=0; i=0; "
    "printf '%s' \"$c\" | grep -qF '\\v' && v=1; printf '%s' \"$c\" | grep -qF '\\r' && r=1; "
    "printf '%s' \"$c\" | grep -qF '\\m' && m=1; printf '%s' \"$c\" | grep -qF '\\s' && s=1; "
    "[ -n \"$I\" ] && printf '%s' \"$c\" | grep -qiwF -- \"$I\" && i=1; "
    "echo \"MO|$f|tipo=$tp|legible=1|bytes=$b|esc_v=$v|esc_r=$r|esc_m=$m|esc_s=$s|id_so=$i\"; done; echo 'MO_FIN'"
)

# pwquality (solo 5.3.3.2.2 y 5.3.3.2.3): únicamente las 6 claves del Audit CIS; nada más sale del host (ni difok).
CLAVES_PWQUALITY_1_3 = ("minlen", "minclass", "dcredit", "ucredit", "lcredit", "ocredit")
PWQUALITY = (
    "for f in /etc/security/pwquality.conf /etc/security/pwquality.conf.d/*.conf; do [ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
    "if [ ! -f \"$f\" ] || [ ! -r \"$f\" ]; then echo \"PE|$f|no_procesable\"; continue; fi; echo \"PF|$f\"; "
    "awk -v F=\"$f\" '{l=tolower($0)} l ~ /^[ \\t]*(" + "|".join(CLAVES_PWQUALITY_1_3) + ")[ \\t]*=/ "
    "{k=l; sub(/^[ \\t]*/,\"\",k); sub(/[ \\t]*=.*/,\"\",k); v=l; sub(/^[^=]*=[ \\t]*/,\"\",v); sub(/[ \\t#].*$/,\"\",v); "
    "if (v !~ /^-?[0-9]+$/) v=\"NO_NUMERICO\"; print \"PQ|\" F \"|\" FNR \"|\" k \"|\" v}' \"$f\" || echo \"PE|$f|error_lectura\"; "
    "done; echo 'PQ_FIN'"
)

# --- 1.4.0 ------------------------------------------------------------------------------------------------------
RSYSLOG_VALIDACION = (
    "R=/usr/sbin/rsyslogd; if [ ! -x \"$R\" ]; then echo 'RN|NO_INSTALADO'; exit 0; fi; "
    "o=\"$(timeout 60 \"$R\" -N1 2>&1)\"; rc=$?; printf '%s\\n' \"$o\" | head -n 60 | sed 's/^/RN_OUT|/'; echo \"RN_RC|$rc\""
)
LOGROTATE = (
    "for f in /etc/logrotate.conf /etc/logrotate.d/*; do [ -f \"$f\" ] || continue; "
    "if [ ! -r \"$f\" ]; then echo \"LE|$f|no_legible\"; continue; fi; "
    "grep -nvE '^[[:space:]]*(#|$)' -- \"$f\" | head -n 400 | while IFS= read -r l; do printf 'LC|%s|%s\\n' \"$f\" \"$l\"; done; done; "
    "L=/usr/sbin/logrotate; if [ -x \"$L\" ]; then o=\"$(timeout 60 \"$L\" -d /etc/logrotate.conf 2>&1)\"; rc=$?; "
    "printf '%s\\n' \"$o\" | grep -E '^(reading config file|error|warning|rotating pattern|considering log|Handling)' | head -n 800 | "
    "sed 's/^/LD|/'; echo \"LD_RC|$rc\"; else echo 'LD|NO_INSTALADO'; fi; echo 'L_FIN'"
)
JOURNAL_ARCHIVOS = (
    "for d in /var/log/journal /run/log/journal; do if [ ! -d \"$d\" ]; then echo \"JD|$d|NO_EXISTE\"; continue; fi; "
    "find \"$d\" -maxdepth 2 \\( -type d -o -type f \\) -printf 'J|%y|%#m|%u|%g|%p\\n' | head -n 500; done; echo 'J_FIN'"
)
UNCONFINED_DETALLE = (
    "ps -e -o label=,pid=,comm= | awk '$1 ~ /:unconfined_service_t:/ {print $2 \"|\" $3 \"|\" $1}' | head -n 200 | "
    "while IFS='|' read -r p c l; do e=\"$(readlink -e -- \"/proc/$p/exe\")\" || e=''; k=''; "
    "[ -n \"$e\" ] && k=\"$(rpm -qf \"$e\" 2>&1 | head -n 1)\"; "
    "printf 'UP|%s|%s|%s|%s|%s\\n' \"$p\" \"$c\" \"$l\" \"${e:-NO_DISPONIBLE}\" \"${k:-NO_DISPONIBLE}\"; done; echo 'UP_FIN'"
)
# Archivos SUID/SGID que la base RPM declara (tipo 10 = regular; dígito especial 2..7) y verificación de sus paquetes.
# `rpm -V --noscripts --nodeps`: nunca ejecuta %verifyscript; solo compara metadatos/digest con la base RPM.
SUID_RPM = (
    "L=\"$(rpm -qa --qf '[%{FILEMODES:octal}|%{FILENAMES}|%{=NAME}\\n]' | awk -F'|' '{n=length($1)} "
    "substr($1,1,n-4)==\"10\" && substr($1,n-3,1) ~ /[2-7]/ {print $1 \"|\" $2 \"|\" $3}' | sort -u | head -n 2000)\"; "
    "printf '%s\\n' \"$L\" | sed '/^$/d; s/^/RS|/'; "
    "for k in $(printf '%s\\n' \"$L\" | awk -F'|' 'NF==3{print $3}' | sort -u); do "
    "rpm -V --noscripts --nodeps \"$k\" 2>&1 | head -n 300 | sed \"s#^#RV|$k|#\"; done; echo 'RS_FIN'"
)
DIRS_PATH_ROOT = ("/root/.local/bin", "/root/bin", "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
ROOT_PATH_DIRS = (
    "for d in " + " ".join(DIRS_PATH_ROOT) + "; do if [ -e \"$d\" ] || [ -L \"$d\" ]; then t=\"$(readlink -e -- \"$d\")\" || t='ROTO'; "
    "l=0; [ -L \"$d\" ] && l=1; printf 'PD|%s|enlace=%s|destino=%s|%s\\n' \"$d\" \"$l\" \"$t\" \"$(stat -Lc '%#a|%U|%G|%F' -- \"$d\" 2>&1)\"; "
    "else echo \"PD|$d|NO_EXISTE\"; fi; done; echo 'PD_FIN'"
)

# --- 1.5.0 ------------------------------------------------------------------------------------------------------
# PRE-3 (prerrequisito interno): llaves autorizadas de las cuentas de automatización. Lista CERRADA de cuentas (la misma de
# config/prerrequisitos_parte1.yml -> PRE-3; cambiarla exige un recolector nuevo). Archivos según AuthorizedKeysFile
# efectivo (sshd -T, %h/%u/%U y rutas relativas al home). Solo sale del host: metadatos (tipo, modo, propietario) y, por
# llave, bits, fingerprint SHA256 y tipo (`ssh-keygen -l`); nunca la llave, su comentario ni sus opciones.
USUARIOS_LLAVES = ("iacolcoauto", "ansible_test")
AUTHORIZED_KEYS = (
    "S=/usr/sbin/sshd; [ -x \"$S\" ] || S=/usr/bin/sshd; if [ ! -x \"$S\" ]; then echo 'AKCFG|SSHD_NO_INSTALADO'; echo 'AK_FIN'; exit 0; fi; "
    "T=\"$(\"$S\" -T)\"; t=$?; if [ \"$t\" -ne 0 ]; then echo \"AKCFG|SSHD_T_RC|$t\"; echo 'AK_FIN'; exit 0; fi; "
    "F=\"$(printf '%s\\n' \"$T\" | awk '$1==\"authorizedkeysfile\"{$1=\"\"; sub(/^ +/,\"\"); print; exit}')\"; "
    "K=\"$(printf '%s\\n' \"$T\" | awk '$1==\"authorizedkeyscommand\"{print ($2==\"none\")?0:1; exit}')\"; "
    "M=\"$(printf '%s\\n' \"$T\" | awk '$1==\"strictmodes\"{print $2; exit}')\"; "
    "echo \"AKCFG|archivos=$F|comando=${K:-0}|strictmodes=${M:-NO_DETERMINADO}\"; "
    "for u in " + " ".join(USUARIOS_LLAVES) + "; do e=\"$(getent passwd \"$u\")\" || { echo \"AKU|$u|NO_EXISTE\"; continue; }; "
    "h=\"$(printf '%s' \"$e\" | cut -d: -f6)\"; i=\"$(printf '%s' \"$e\" | cut -d: -f3)\"; "
    "echo \"AKU|$u|existe|home=$h|$(stat -Lc '%#a|%U|%G' -- \"$h\" 2>&1)\"; "
    "set -f; for p in $F; do [ \"$p\" = none ] && continue; p=\"${p//%h/$h}\"; p=\"${p//%u/$u}\"; p=\"${p//%U/$i}\"; "
    "case \"$p\" in /*) f=\"$p\";; *) f=\"$h/$p\";; esac; "
    "if [ ! -e \"$f\" ] && [ ! -L \"$f\" ]; then echo \"AKA|$u|$f|NO_EXISTE\"; continue; fi; "
    "l=0; [ -L \"$f\" ] && l=1; n=\"$(grep -cvE '^[[:space:]]*(#|$)' -- \"$f\")\"; "
    "echo \"AKA|$u|$f|enlace=$l|$(stat -Lc '%F|%#a|%U|%G' -- \"$f\" 2>&1 | tr ' ' '_')|dir=$(stat -Lc '%#a|%U|%G' -- \"${f%/*}\" 2>&1)|lineas=${n:-0}\"; "
    "[ -f \"$f\" ] || continue; o=\"$(timeout 30 ssh-keygen -l -E sha256 -f \"$f\")\"; r=$?; "
    "printf '%s\\n' \"$o\" | awk -v U=\"$u\" -v A=\"$f\" 'NF>=3 && $2 ~ /^SHA256:/ {print \"AKK|\" U \"|\" A \"|\" $1 \"|\" $2 \"|\" $NF}'; "
    "[ \"$r\" -ne 0 ] && echo \"AKE|$u|$f|ssh_keygen_rc=$r\"; done; set +f; done; echo 'AK_FIN'"
)

COMANDOS_PRIV = [
    Comando("priv.ssh_efectiva", "Configuración efectiva de sshd (sshd -t / -T), lista blanca de claves CIS y bloques Match",
            SSH_EFECTIVA, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="sshd -T incluye rutas de HostKey y listas de usuarios; los criterios Match pueden identificar usuarios/IP",
            redaccion="Solo se emiten las claves CIS de la lista blanca; allow/deny y criterios Match sin valores."),
    Comando("priv.sudo_politica", "Política sudo normalizada (use_pty, timestamp_timeout, NOPASSWD, !authenticate, instrumentación)",
            SUDO_POLITICA, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="sudoers contiene reglas, comandos y alias", redaccion="Nunca se emite una línea de sudoers: solo indicadores, archivo/línea/titular y SHA-256."),
    Comando("priv.cuentas", "Envejecimiento y bloqueo por cuenta (/etc/shadow sin campo de contraseña) e INACTIVE por defecto",
            CUENTAS, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="Hashes de contraseña en /etc/shadow", redaccion="El campo 2 se clasifica dentro de awk (0/1) y nunca se imprime."),
    # 1.3.0
    Comando("priv.kmod_config", "Directivas install/blacklist efectivas de modprobe.d para 6 módulos CIS (sin invocar modprobe)",
            KMOD_CONFIG, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="modprobe.d puede contener comandos install arbitrarios", redaccion="Solo módulo, tipo de directiva y destino true/false/OTRO."),
    Comando("priv.motd", "Indicadores de información del sistema en /etc/motd y /etc/motd.d (CIS 1.7.1), sin el texto",
            MOTD, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="Texto de los banners", redaccion="Nunca se emite el texto: solo indicadores 0/1, tipo y tamaño."),
    Comando("priv.pwquality", "Claves minlen/minclass/[dulo]credit de pwquality.conf(.d) (CIS 5.3.3.2.2 y 5.3.3.2.3)",
            PWQUALITY, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="Política de contraseñas", redaccion="Solo 6 claves con valor entero; el resto (difok, dictcheck...) se descarta."),
    # 1.4.0
    Comando("priv.rsyslog_validacion", "Validación de sintaxis de rsyslog (rsyslogd -N1; no arranca el demonio) (CIS 6.2.3.5)",
            RSYSLOG_VALIDACION, familias=AMBAS, sudo=True, rc_validos=None, timeout=90,
            sensible="Mensajes de rsyslogd pueden citar parámetros de configuración", redaccion="Filtro de redacción del recolector."),
    Comando("priv.logrotate", "Configuración de logrotate sin comentarios y logrotate -d (modo depuración, sin cambios) (CIS 6.2.3.8)",
            LOGROTATE, familias=AMBAS, sudo=True, rc_validos=None, timeout=90,
            sensible="Scripts postrotate", redaccion="Filtro de redacción del recolector; solo líneas de configuración y del modo depuración."),
    Comando("priv.journal_archivos", "Tipo, modo, propietario y grupo de /var/log/journal y /run/log/journal (CIS 6.2.1.2)",
            JOURNAL_ARCHIVOS, familias=AMBAS, sudo=True, rc_validos=None,
            redaccion="Solo metadatos (nunca contenido de journals)."),
    Comando("priv.unconfined_detalle", "Procesos unconfined_service_t: PID, comm, contexto, ejecutable y paquete RPM (CIS 1.3.1.6)",
            UNCONFINED_DETALLE, familias=(RHEL,), sudo=True, rc_validos=None,
            redaccion="Solo PID, nombre, contexto, ruta del ejecutable y nombre del paquete (nunca argumentos ni entorno)."),
    Comando("priv.suid_rpm", "Archivos SUID/SGID de la base RPM y rpm -V --noscripts de sus paquetes (CIS 7.1.13)",
            SUID_RPM, familias=(RHEL,), sudo=True, rc_validos=None, timeout=300,
            redaccion="Solo rutas, modos, nombres de paquete e indicadores de rpm -V."),
    Comando("priv.root_path_dirs", "readlink -e y stat -L de los directorios candidatos del PATH de root (CIS 5.4.2.5, R6)",
            ROOT_PATH_DIRS, familias=AMBAS, sudo=True, rc_validos=None,
            redaccion="Solo metadatos de directorios; nunca se ejecuta el perfil de root."),
    # 1.5.0
    Comando("priv.authorized_keys", "Llaves autorizadas de las cuentas de automatización: fingerprint SHA256 y metadatos (PRE-3)",
            AUTHORIZED_KEYS, familias=AMBAS, sudo=True, rc_validos=None,
            sensible="authorized_keys contiene llaves públicas, comentarios (usuario@host) y opciones (from=, command=)",
            redaccion="Solo bits, fingerprint SHA256 y tipo por llave (ssh-keygen -l); nunca la llave, su comentario ni sus opciones."),
]
SOLO_PRIVILEGIADO = {c.id for c in COMANDOS_PRIV}
assert len(SYSCTL_P1) == 28
