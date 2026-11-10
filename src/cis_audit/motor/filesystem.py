"""Recolector de filesystem profundo `cis-audit-collect-fs` (instalación: docs/Instalacion_Collectors.pdf).

Recolector INDEPENDIENTE del principal (cis-audit-collect 1.3.0 no cambia). Se ejecuta solo de forma explícita
(`auditar.py ejecutar --filesystem-profundo`), durante una ventana autorizada, con su propio digest en sudoers
(/etc/sudoers.d/cis_audit_fs, temporal). Este proyecto NUNCA lo instala.

Propiedades del script (verificadas por problemas_script y tests/test_recolector_fs.py):
- sin argumentos, stdin cerrado, entorno fijo (env de root mínimo, LC_ALL=C), umask 0077, `bash -p`, requiere root;
- solo METADATOS: `find -xdev ... -printf` (tipo, modo, propietario, grupo, ruta). Nunca lee ni emite contenido
  de los archivos encontrados (ni -exec, -ok, -delete, -fprint, cat, head...);
- selección de montajes y exclusiones LITERALES del Audit CIS OL9 v2.0.0 (7.1.11, 7.1.12, 7.1.13, 6.3.3.6, 7.2.9);
- UN recorrido por montaje para todas las categorías (ramas con el operador ',' de find);
- límite por categoría y montaje con TRUNCAMIENTO DETERMINISTA: se conservan las L rutas menores en orden de bytes
  (búfer acotado a 2L, compactado con asort), de modo que el subconjunto no depende del orden de recorrido de find;
  el recuento total es exacto y la salida final está ordenada;
- timeouts global, por montaje y por home; prioridad nice 19 / ionice idle;
- redacción fail-closed (autoprueba + PIPESTATUS) como en el recolector principal; sin archivos temporales.
- 1.1.0: cada error de find se emite con su ruta y mensaje (MERR/DERR). Si la ruta es exactamente un punto de montaje de
  OTRO sistema de archivos bajo el recorrido (con -xdev find no entra en él, solo lo consulta; p. ej. un FUSE gvfs huérfano
  'Transport endpoint is not connected'), se clasifica montaje_ajeno y no cuenta como evidencia faltante. Cualquier otro
  error sigue dejando el recorrido incompleto. Ese otro montaje, si está en el alcance CIS, se recorre y responde por sí mismo.
"""

import hashlib
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .recolector import Registro

RUTA_FS = "/usr/local/libexec/cis-audit/cis-audit-collect-fs"
VERSION_FS = "1.1.0"
FORMATO_FS = "fs1"
LIMITE = 5000                 # entradas por categoría y montaje (y por usuario en DOT)
TIMEOUT_GLOBAL = 3600         # = ejecucion.timeout_filesystem_seg
TIMEOUT_MONTAJE = 1800
TIMEOUT_HOME = 300
CATEGORIAS = ("WW", "NU", "NG", "SG", "DOT")

# Audit CIS OL9 v2.0.0 (literal). Tipos excluidos (prefijo, como el awk del Audit): 7.1.11, 7.1.12 y 7.1.13.
RE_TIPOS_EXCLUIDOS = r"^[[:space:]]*(nfs|proc|smb|vfat|iso9660|efivarfs|selinuxfs)"   # \s del Audit (POSIX, apto para awk -v)
RE_DESTINO_7111 = r"^(/run/user/|/tmp|/var/tmp)"
RE_DESTINO_RUN_USER = r"^/run/user/"
RUTAS_EXCL_7111 = ("/run/user/*", "/proc/*", "*/containerd/*", "*/kubelet/pods/*", "*/kubelet/plugins/*", "/sys/*", "/snap/*")
RUTAS_EXCL_7112 = ("/run/user/*", "/proc/*", "*/containerd/*", "*/kubelet/pods/*", "*/kubelet/plugins/*",
                   "/sys/fs/cgroup/memory/*", "/var/*/private/*")

_REDACCION_FS = [r"s#\$(1|2[abxy]?|5|6|y|gy|7)\$[^[:space:]:]{8,}#<REDACTADO>#g",
                 r"s#-----BEGIN [A-Z ]*PRIVATE KEY-----.*#<REDACTADO>#"]


def _excl(rutas) -> str:
    return " -a ".join(f'! -path {shlex.quote(r)}' for r in rutas)


# Programa gawk: entrada NUL ("TAG\ttipo\tmodo\tusuario\tgrupo\truta"); conserva por categoría las L rutas MENORES
# (orden de bytes, LC_ALL=C) con un búfer de como mucho 2L; recuento exacto; salida ordenada.
_AWK_TOPL = r'''
function esc(s) { gsub(/\\/, "\\\\", s); gsub(/\n/, "\\n", s); gsub(/\|/, "\\x7c", s); gsub(/\t/, "\\t", s); return s }
function compacta(t,   n, i, a) { n = asort(b[t], a); delete b[t]; for (i = 1; i <= n && i <= L; i++) b[t][i] = a[i]; c[t] = (n < L) ? n : L }
NF >= 6 { t = $1; p = $6; for (i = 7; i <= NF; i++) p = p "\t" $i; r = esc(p) "\t" $2 "\t" $3 "\t" esc($4) "\t" esc($5); n[t]++; b[t][++c[t]] = r; if (c[t] >= 2 * L) compacta(t) }
END {
  for (k = 1; k <= split(TAGS, T, ","); k++) { t = T[k]; if (!(t in c)) continue; compacta(t)
    for (i = 1; i <= c[t]; i++) { split(b[t][i], f, "\t"); print "E|" t "|" f[2] "|" f[3] "|" f[4] "|" f[5] "|" f[1] (t == "SG" ? X : "") } }
  printf "%s|%s", CNT, ID; for (k = 1; k <= split(TAGS, T, ","); k++) printf "|%s=%d", tolower(T[k]), n[T[k]] + 0
  tr = 0; for (k = 1; k <= split(TAGS, T, ","); k++) if (n[T[k]] > L) tr = 1; printf "|trunc=%d\n", tr
}'''


def generar_fs(timeout_global: int = TIMEOUT_GLOBAL, limite: int = LIMITE) -> str:
    sed = " ".join(f"-e {shlex.quote(e)}" for e in _REDACCION_FS)
    awk = shlex.quote(_AWK_TOPL.strip())
    return f'''#!/bin/bash -p
# =============================================================================
# cis-audit-collect-fs {VERSION_FS} — Recolector de FILESYSTEM PROFUNDO para auditoría CIS (SOLO LECTURA)
# =============================================================================
# Generado por cis_audit (python3 src/auditar.py generar-recolector-fs). NO EDITAR: cualquier cambio altera el
# SHA-256 autorizado en /etc/sudoers.d/cis_audit_fs y el precheck dejará de usarlo.
# Uso TEMPORAL: se instala solo durante una ventana autorizada de filesystem profundo y se retira al terminar.
#   {RUTA_FS}  root:root 0755
#   sudoers: srvinnops ALL=(root) NOPASSWD: sha256:<digest> {RUTA_FS} ""
# Garantías: sin argumentos ni stdin; solo metadatos (find -printf), nunca contenido; sin archivos temporales;
# montajes y exclusiones literales del Audit CIS (7.1.11, 7.1.12, 7.1.13, 6.3.3.6, 7.2.9); límite {limite} por
# categoría con truncamiento determinista; timeouts global {timeout_global}s / montaje {TIMEOUT_MONTAJE}s / home {TIMEOUT_HOME}s.
# =============================================================================
set -u
umask 0077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
LANG=C
IFS=$' \\t\\n'
export PATH LC_ALL LANG
unset BASH_ENV ENV CDPATH GLOBIGNORE PS4
exec 0</dev/null

if [ "$#" -ne 0 ]; then echo "@@CIS_FATAL|argumentos_no_admitidos"; exit 2; fi
if [ "$(id -u)" != "0" ]; then echo "@@CIS_FATAL|requiere_root"; exit 2; fi
case "$(awk --version 2>&1)" in "GNU Awk "*) ;; *) echo "@@CIS_FATAL|awk_no_gnu"; exit 2 ;; esac

__osv() {{ grep -m1 -E "^$1=" /etc/os-release | cut -d= -f2- | tr -d '"'; }}
__id="$(__osv ID)"; __ver="$(__osv VERSION_ID)"; __major="${{__ver%%.*}}"
case "$__id:$__major" in
  rhel:8) __bench=RHEL8 ;; rhel:9) __bench=RHEL9 ;; ol:8) __bench=ORACLE8 ;; ol:9) __bench=ORACLE9 ;; sles:15) __bench=SLES15 ;;
  *) echo "@@CIS_FATAL|sistema_no_soportado|detectado=$__id $__ver"; exit 0 ;;
esac

__redactar() {{
  sed -u -E {sed}
}}
__canario="$(printf '%s\\n' 'h=$6$CANARIOcanario01234' | __redactar)" || __canario=""
if [ "$__canario" != 'h=<REDACTADO>' ]; then echo "@@CIS_FATAL|redaccion_no_operativa"; exit 3; fi

__AWK={awk}
__L={limite}
__F='\\t%y\\t%#m\\t%u\\t%g\\t%p\\0'   # -printf interpreta \\t y \\0 (en $'...' el NUL cortaría la cadena)

# 1.1.0 — errores de find: un punto de montaje de OTRO sistema de archivos bajo la ruta recorrida (-xdev no entra en él,
# solo lo consulta) se registra como montaje_ajeno y no cuenta; cualquier otro error cuenta (fail-closed). Siempre se
# emiten ruta y mensaje. Variables de salida: n (errores), v (desaparecidos), a (montajes ajenos).
__RE_FINDERR="^(.*/)?find: ('(.*)'|‘(.*)’): (.*)$"
__clasificar() {{
  local base="$1" tag="$2" id="$3" err="$4" sub l p msg
  n=0; v=0; a=0
  sub="$(findmnt -n -r -k -o TARGET | while IFS= read -r t; do printf '%b\n' "$t"; done | awk -v b="$base" '$0 != b && (b == "/" || index($0, b "/") == 1)')"
  while IFS= read -r l; do
    case "$l" in ""|@@RC=*) continue ;; esac
    case "$l" in *"No such file or directory"*) v=$((v + 1)); continue ;; esac
    p=""; msg="$l"
    if [[ "$l" =~ $__RE_FINDERR ]]; then p="${{BASH_REMATCH[3]}}${{BASH_REMATCH[4]}}"; msg="${{BASH_REMATCH[5]}}"; fi
    if [ -n "$p" ] && printf '%s\n' "$sub" | grep -qxF -- "$p"; then
      a=$((a + 1)); printf '%sERR|%s|montaje_ajeno|%s|%s\n' "$tag" "${{id//|/%7C}}" "${{p//|/%7C}}" "${{msg//|/%7C}}"
    else
      n=$((n + 1)); printf '%sERR|%s|error|%s|%s\n' "$tag" "${{id//|/%7C}}" "${{p//|/%7C}}" "${{msg//|/%7C}}"
    fi
  done <<< "$err"
}}

# Recorrido único de un montaje: $1 destino, $2 ww, $3 nu, $4 sg, $5 s13, $6 s6, $7 tipo de fs.
__montaje() {{
  local m="$1" ww="$2" nu="$3" sg="$4" s13="$5" s6="$6" ft="$7" rest tm err rc n
  echo "MNT|$m|$ft|ww=$ww|nu=$nu|s13=$s13|s6=$s6"
  rest=$(( __deadline - $(date +%s) ))
  if [ "$rest" -le 0 ]; then echo "MFIN|$m|NO_INICIADO"; return; fi
  tm={TIMEOUT_MONTAJE}; [ "$rest" -lt "$tm" ] && tm="$rest"
  local e=( "$m" -xdev \\( -false )
  [ "$ww" = 1 ] && e+=( , \\( {_excl(RUTAS_EXCL_7111)} \\) \\( -type f -o -type d \\) -perm -0002 -printf "WW$__F" )
  [ "$nu" = 1 ] && e+=( , \\( {_excl(RUTAS_EXCL_7112)} \\) \\( -type f -o -type d \\) \\( \\( -nouser -printf "NU$__F" \\) , \\( -nogroup -printf "NG$__F" \\) \\) )
  [ "$sg" = 1 ] && e+=( , -type f -perm /6000 -printf "SG$__F" )
  e+=( \\) )
  exec 4>&1
  err=$( {{ timeout "$tm" nice -n 19 ionice -c3 find "${{e[@]}}" | awk -v RS='\\0' -F'\\t' -v L="$__L" -v TAGS=WW,NU,NG,SG \\
           -v CNT=MCNT -v ID="$m" -v X="|s13=$s13|s6=$s6" "$__AWK" 1>&4; printf '@@RC=%s %s\\n' "${{PIPESTATUS[0]}}" "${{PIPESTATUS[1]}}" >&2; }} 2>&1 )
  exec 4>&-
  rc="$(printf '%s\\n' "$err" | sed -n 's/^@@RC=\\([0-9]*\\) .*/\\1/p')"; ra="$(printf '%s\\n' "$err" | sed -n 's/^@@RC=[0-9]* //p')"
  __clasificar "$m" M "$m" "$err"
  echo "MFIN|$m|rc_find=${{rc:-255}}|rc_awk=${{ra:-255}}|timeout=$([ "$rc" = 124 ] && echo 1 || echo 0)|errores=$n|desaparecidos=$v|ajenos=$a"
}}

{{
echo "@@CIS_RECOLECTOR_FS|formato={FORMATO_FS}|version={VERSION_FS}|benchmark=$__bench|limite=$__L|timeout_global={timeout_global}|timeout_montaje={TIMEOUT_MONTAJE}|timeout_home={TIMEOUT_HOME}"
__deadline=$(( $(date +%s) + {timeout_global} ))
# 6.3.3.6 (Audit CIS): filesystems con dispositivo (tipos no 'nodev') sin noexec/nosuid.
__nd="$(awk '/nodev/ {{ print $2 }}' /proc/filesystems | paste -sd,)"
__s6="$(findmnt -n -r -k -it "$__nd" -o TARGET,SOURCE,FSTYPE,OPTIONS | grep -Pv 'noexec|nosuid' | awk '{{print $1}}')"
# 7.1.11 / 7.1.12 / 7.1.13 (Audit CIS): findmnt -Dkerno fstype,target,options y filtros literales; se unen con 6.3.3.6.
while IFS='|' read -r m ft ww nu s13 s6; do
  sg=0; {{ [ "$s13" = 1 ] || [ "$s6" = 1 ]; }} && sg=1
  {{ [ "$ww" = 1 ] || [ "$nu" = 1 ] || [ "$sg" = 1 ]; }} || continue
  __montaje "$(printf '%b' "$m")" "$ww" "$nu" "$sg" "$s13" "$s6" "$ft"
done < <( {{ findmnt -Dkerno fstype,target,options; printf '%s\\n' "$__s6" | awk 'NF {{print "@S6 " $1 " -"}}'; findmnt -n -r -k -o FSTYPE,TARGET; }} \\
          | awk -v XT='{RE_TIPOS_EXCLUIDOS}' -v XW='{RE_DESTINO_7111}' -v XU='{RE_DESTINO_RUN_USER}' '
  $1 == "@S6" {{ s6[$2] = 1; next }}
  NF == 2 {{ if (!($2 in ft)) ft[$2] = $1; next }}
  NF >= 3 {{ t = $2; ft[t] = $1; ex = ($1 ~ XT); ww[t] = (!ex && t !~ XW); nu[t] = (!ex && t !~ XU); s13[t] = (!ex && t !~ XU && $3 !~ /noexec/ && $3 !~ /nosuid/); vis[t] = 1 }}
  END {{ for (t in vis) all[t] = 1; for (t in s6) all[t] = 1; n = asorti(all, o)
         for (i = 1; i <= n; i++) {{ t = o[i]; printf "%s|%s|%d|%d|%d|%d\\n", t, (t in ft ? ft[t] : "?"), ww[t] + 0, nu[t] + 0, s13[t] + 0, (t in s6) }} }}' )

# 7.2.9 (Audit CIS): dotfiles de usuarios con shell válida (/etc/shells sin nologin).
__vs="^($(awk -F/ '$NF != "nologin" && /^\\// {{print}}' /etc/shells | sed -e 's/[.[\\*^$]/\\\\&/g' | paste -sd'|' -))$"
while IFS=' ' read -r u h; do
  [ -d "$h" ] || continue
  g="$(id -gn -- "$u" 2>&1)" || g="?"
  echo "DUSR|$u|$g|$h"
  rest=$(( __deadline - $(date +%s) ))
  if [ "$rest" -le 0 ]; then echo "DFIN|$u|NO_INICIADO"; continue; fi
  tm={TIMEOUT_HOME}; [ "$rest" -lt "$tm" ] && tm="$rest"
  exec 4>&1
  err=$( {{ timeout "$tm" nice -n 19 ionice -c3 find "$h" -xdev -type f -name '.*' -printf "DOT$__F" | awk -v RS='\\0' -F'\\t' -v L="$__L" \\
           -v TAGS=DOT -v CNT=DCNT -v ID="$u" -v X="" "$__AWK" 1>&4; printf '@@RC=%s %s\\n' "${{PIPESTATUS[0]}}" "${{PIPESTATUS[1]}}" >&2; }} 2>&1 )
  exec 4>&-
  rc="$(printf '%s\\n' "$err" | sed -n 's/^@@RC=\\([0-9]*\\) .*/\\1/p')"; ra="$(printf '%s\\n' "$err" | sed -n 's/^@@RC=[0-9]* //p')"
  __clasificar "$h" D "$u" "$err"
  echo "DFIN|$u|rc_find=${{rc:-255}}|rc_awk=${{ra:-255}}|timeout=$([ "$rc" = 124 ] && echo 1 || echo 0)|errores=$n|desaparecidos=$v|ajenos=$a"
done < <(awk -v pat="$__vs" -F: '$(NF) ~ pat {{ print $1 " " $(NF-1) }}' /etc/passwd)

# Instrumentación temporal (D-3B-01): identidad del sudoers FS instalado (bytes exactos), nunca su contenido.
f=/etc/sudoers.d/cis_audit_fs
if [ -e "$f" ] || [ -L "$f" ]; then echo "INSTR_FS|existe=1|$(stat -c '%a|%u|%g' -- "$f")|tipo=$(stat -c '%F' -- "$f" | tr ' ' '_')|sha_archivo=$(sha256sum < "$f" | cut -d' ' -f1)"
else echo "INSTR_FS|existe=0"; fi
echo "@@CIS_FS_FIN"
}} 2>&1 | __redactar
__st=("${{PIPESTATUS[@]}}")
if [ "${{__st[1]}}" -ne 0 ]; then printf '\\n@@CIS_FATAL|redaccion_fallida|rc=%s\\n' "${{__st[1]}}"; exit 3; fi
if [ "${{__st[0]}}" -ne 0 ]; then printf '\\n@@CIS_FATAL|recoleccion_interrumpida|rc=%s\\n' "${{__st[0]}}"; exit 3; fi
exit 0
'''


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def sudoers_fs(digest: str) -> str:
    plantilla = (Path(__file__).resolve().parents[3] / "templates" / "sudoers_cis_audit_fs.plantilla").read_text(encoding="utf-8")
    return plantilla.replace("<SHA256_DEL_SCRIPT_FS>", digest)


def problemas_script_fs(texto: str) -> List[str]:
    """Verificaciones estáticas del script FS (las ejecutan las pruebas y el generador)."""
    from .seguridad import problemas_comando
    p = []
    cuerpo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
    cuerpo = re.sub(r"__AWK='.*?'\n", "__AWK=PROGRAMA_AWK\n", cuerpo, flags=re.S)   # el programa awk contiene comparaciones '>'
    if re.search(r"(^|[\s;&|(])sudo\b", cuerpo):
        p.append("invoca sudo")
    for x in ("-exec", "-execdir", "-ok ", "-okdir", "-delete", "-fprint", "-fls", "mktemp", "/tmp/", "sort -T", " eval ", "source ",
              "bash -l", "-Hiu", " su "):
        if x in cuerpo:
            p.append(f"contiene '{x.strip()}'")
    if re.search(r"\b(cat|head|tail|less|more|xargs|tee|dd|cp|mv|rm|chmod|chown|setfacl)\b", cuerpo):
        p.append("binario no permitido (lectura de contenido o modificación)")
    # Redirecciones de salida: fuera de comillas simples (sed/awk usan '<' '>' como texto o comparación).
    sin_comillas = re.sub(r"'[^']*'", "''", re.sub(r"[0-9]?>&[0-9-]|>\(|<\(", "", cuerpo))
    if re.search(r"(?<![0-9&])>(?!&)\s*[^&\s]", sin_comillas):
        p.append("redirección a archivo")
    if cuerpo.count("find ") != 2 or cuerpo.count("-xdev") != 2 or "-printf" not in cuerpo or \
            cuerpo.count('timeout "$tm" nice -n 19 ionice -c3 find ') != 2:
        p.append("find: debe haber exactamente dos recorridos (-xdev, -printf, timeout + nice + ionice)")
    for d in ('[ "$#" -ne 0 ]', "exec 0</dev/null", "umask 0077", "redaccion_no_operativa", "PIPESTATUS", "requiere_root", "@@CIS_FS_FIN",
              "nice -n 19 ionice -c3", "awk_no_gnu"):
        if d not in texto:
            p.append(f"falta '{d}'")
    for prob in problemas_comando(re.sub(r"'[^']*'", "''", cuerpo)):
        if "sudo" not in prob:
            p.append(prob)
    return p


# ============================================================================== parser (controlador)
_RE_ENT = re.compile(r"^E\|(WW|NU|NG|SG|DOT)\|([fdlspcbD])\|(0[0-7]{3,5})\|([^|]*)\|([^|]*)\|([^|]+)(\|s13=[01]\|s6=[01])?$")


def parsear_fs(texto: str) -> Dict:
    try:
        return _parsear_fs(texto)
    except (ValueError, IndexError) as e:   # valor no numérico o campo ausente: evidencia inválida (fail-closed)
        return {"version": None, "benchmark": None, "limite": None, "montajes": {}, "usuarios": {}, "instr_fs": None,
                "fin": False, "problema": f"línea mal formada ({type(e).__name__})"}


def _parsear_fs(texto: str) -> Dict:
    """Salida fs1 -> estructura validada. Formato CERRADO: cualquier línea desconocida invalida la evidencia
    (problema != None), igual que la falta de @@CIS_FS_FIN o un @@CIS_FATAL (fail-closed)."""
    res = {"version": None, "benchmark": None, "limite": None, "montajes": {}, "usuarios": {}, "instr_fs": None,
           "fin": False, "problema": None}
    actual_m = actual_u = None
    for l in texto.replace("\r\n", "\n").split("\n"):
        if not l:
            continue
        if l.startswith("@@CIS_FATAL|"):
            res["problema"] = f"@@CIS_FATAL|{l.split('|', 1)[1]}"; return res
        if l.startswith("@@CIS_RECOLECTOR_FS|"):
            kv = dict(x.split("=", 1) for x in l.split("|")[1:] if "=" in x)
            if kv.get("formato") != FORMATO_FS:
                res["problema"] = "formato desconocido"; return res
            res.update(version=kv.get("version"), benchmark=kv.get("benchmark"), limite=int(kv.get("limite", "0") or 0)); continue
        if l == "@@CIS_FS_FIN":
            res["fin"] = True; continue
        p = l.split("|")
        if p[0] == "MNT" and len(p) == 7:
            actual_m, actual_u = p[1], None
            res["montajes"][p[1]] = {"fstype": p[2], **{x.split("=")[0]: x.split("=")[1] == "1" for x in p[3:7]},
                                     "entradas": [], "cnt": None, "fin": None}
            continue
        if p[0] == "DUSR" and len(p) == 4:
            actual_u, actual_m = p[1], None
            res["usuarios"][p[1]] = {"grupo": p[2], "home": p[3], "entradas": [], "cnt": None, "fin": None}
            continue
        m = _RE_ENT.match(l)
        if m:
            tag = m.group(1)
            dest = res["usuarios"].get(actual_u) if tag == "DOT" else res["montajes"].get(actual_m)
            if dest is None:
                res["problema"] = f"entrada fuera de contexto: {l[:80]}"; return res
            dest["entradas"].append({"tag": tag, "tipo": m.group(2), "modo": m.group(3), "usuario": m.group(4), "grupo": m.group(5),
                                     "ruta": m.group(6), "s13": (m.group(7) or "").startswith("|s13=1"), "s6": (m.group(7) or "").endswith("s6=1")})
            continue
        if p[0] in ("MCNT", "DCNT") and len(p) >= 3:
            dest = res["montajes"].get(p[1]) if p[0] == "MCNT" else res["usuarios"].get(p[1])
            if dest is None:
                res["problema"] = "recuento fuera de contexto"; return res
            dest["cnt"] = {x.split("=", 1)[0]: int(x.split("=", 1)[1]) for x in p[2:]}
            continue
        if p[0] in ("MFIN", "DFIN") and len(p) >= 3:
            dest = res["montajes"].get(p[1]) if p[0] == "MFIN" else res["usuarios"].get(p[1])
            if dest is None:
                res["problema"] = "fin fuera de contexto"; return res
            dest["fin"] = "NO_INICIADO" if p[2] == "NO_INICIADO" else {x.split("=", 1)[0]: int(x.split("=", 1)[1]) for x in p[2:]}
            continue
        if p[0] in ("MERR", "DERR") and len(p) == 5 and p[2] in ("montaje_ajeno", "error"):
            dest = res["montajes"].get(p[1]) if p[0] == "MERR" else res["usuarios"].get(p[1])
            if dest is None:
                res["problema"] = "error de recorrido fuera de contexto"; return res
            dest.setdefault("errores_det", []).append({"clase": p[2], "ruta": p[3].replace("%7C", "|"), "mensaje": p[4].replace("%7C", "|")})
            continue
        if p[0] == "INSTR_FS":
            kv = dict(x.split("=", 1) for x in p[1:] if "=" in x)
            res["instr_fs"] = ({"existe": True, "modo": p[2], "uid": p[3], "gid": p[4], "tipo": kv.get("tipo", "").replace("_", " "),
                                "sha_archivo": kv.get("sha_archivo", "")} if kv.get("existe") == "1" and len(p) >= 7 else {"existe": False})
            continue
        res["problema"] = f"línea inesperada: {l[:80]}"; return res
    if not res["fin"]:
        res["problema"] = "salida incompleta (sin @@CIS_FS_FIN)"
    elif res["version"] is None:
        res["problema"] = "sin cabecera @@CIS_RECOLECTOR_FS"
    return res


def _estado(dest: Dict, tags: Tuple[str, ...]) -> Tuple[bool, bool, int]:
    """(completo, truncado, total) de un montaje/usuario para las categorías indicadas."""
    fin, cnt = dest.get("fin"), dest.get("cnt") or {}
    total = sum(cnt.get(t.lower(), 0) for t in tags)
    if not isinstance(fin, dict) or dest.get("cnt") is None:
        return False, False, total
    trunc = any(cnt.get(t.lower(), 0) > dest.get("_limite", LIMITE) for t in tags)
    # Fail-closed: cualquier rc distinto de 0 (find o awk), timeout, error o archivo desaparecido durante el recorrido
    # (find termina con rc 1) -> recorrido incompleto. Un MFIN sin rc_awk (formato anterior) tampoco es completo.
    # 1.1.0: rc_find 1 se acepta solo si TODOS los errores son puntos de montaje ajenos (ajenos > 0, errores == 0) y cada uno
    # consta con su ruta (MERR/DERR montaje_ajeno).
    ajenos = [e for e in dest.get("errores_det", []) if e["clase"] == "montaje_ajeno"]
    rc_ok = fin.get("rc_find") == 0 or (fin.get("rc_find") == 1 and fin.get("ajenos", 0) > 0 and len(ajenos) == fin.get("ajenos"))
    completo = (rc_ok and fin.get("rc_awk") == 0 and fin.get("timeout") == 0 and fin.get("errores") == 0
                and fin.get("desaparecidos", 0) == 0)
    return completo, trunc, total


def materializar_fs(par: Dict, fecha: str = "") -> Dict[str, Registro]:
    """Traduce la salida fs1 a los registros que ya consumen los evaluadores (mismos formatos de línea):
    fs.world_writable (tipo|modo|ruta), fs.sin_dueno (NOUSER|ruta / NOGROUP|ruta), fs.suid_sgid (subconjunto 7.1.13:
    #MOUNT|fstype|montaje + modo|usuario|grupo|ruta), fsd.privilegiados_6336 (subconjunto 6.3.3.6, mismo formato),
    usr.dotfiles_completo (usuario|grupo|modo|propietario|grupo|ruta) y fsd.completitud (completo/truncado por categoría)."""
    cmd = f"sudo -n {RUTA_FS}"
    if par.get("problema"):
        motivo = f"[RECOLECCION_FS_INVALIDA] {par['problema']}"
        comp = "\n".join(f"CAT|{c}|completo=0|truncado=0|total=0|motivo={motivo}" for c in ("ww", "nu", "sg13", "sg6", "dot"))
        regs = {cid: Registro(id=cid, comando=cmd, stdout="", rc=0, sudo=True, fecha=fecha)
                for cid in ("fs.world_writable", "fs.sin_dueno", "fs.suid_sgid", "fsd.privilegiados_6336", "usr.dotfiles_completo")}
        regs["fsd.completitud"] = Registro(id="fsd.completitud", comando=cmd, stdout=comp, rc=0, sudo=True, fecha=fecha)
        return regs
    for d in list(par["montajes"].values()) + list(par["usuarios"].values()):
        d["_limite"] = par.get("limite") or LIMITE
    ww, nu, sg13, sg6, dot = [], [], [], [], []
    est = {c: {"completo": True, "truncado": False, "total": 0, "inc": [], "ajenos": []} for c in ("ww", "nu", "sg13", "sg6", "dot")}

    def marca(cat, nombre, d, tags):
        c, t, n = _estado(d, tags)
        e = est[cat]
        e["total"] += n
        e["truncado"] |= t
        e["ajenos"] += [f"{x['ruta']} ({x['mensaje']})" for x in d.get("errores_det", []) if x["clase"] == "montaje_ajeno"]
        if not c:
            e["completo"] = False
            det = "; ".join(f"{x['ruta'] or '?'}: {x['mensaje']}" for x in d.get("errores_det", []) if x["clase"] == "error")
            e["inc"].append(f"{nombre}: {d.get('fin') if d.get('fin') else 'sin fin'}" + (f" — errores: {det}" if det else ""))
        if t:
            e["inc"].append(f"{nombre}: truncado")
    for m, d in sorted(par["montajes"].items()):
        if d.get("ww"):
            marca("ww", m, d, ("WW",))
        if d.get("nu"):
            marca("nu", m, d, ("NU", "NG"))
        if d.get("s13"):
            marca("sg13", m, d, ("SG",))
        if d.get("s6"):
            marca("sg6", m, d, ("SG",))
        if d.get("s13"):
            sg13.append(f"#MOUNT|{d['fstype']}|{m}")
        if d.get("s6"):
            sg6.append(f"#MOUNT|{d['fstype']}|{m}")
        for e in d["entradas"]:
            if e["tag"] == "WW" and d.get("ww"):
                ww.append(f"{e['tipo']}|{e['modo']}|{e['ruta']}")
            elif e["tag"] == "NU" and d.get("nu"):
                nu.append(f"NOUSER|{e['ruta']}")
            elif e["tag"] == "NG" and d.get("nu"):
                nu.append(f"NOGROUP|{e['ruta']}")
            elif e["tag"] == "SG":
                fila = f"{e['modo']}|{e['usuario']}|{e['grupo']}|{e['ruta']}"
                if e["s13"]:
                    sg13.append(fila)
                if e["s6"]:
                    sg6.append(fila)
    for u, d in par["usuarios"].items():
        marca("dot", u, d, ("DOT",))
        for e in d["entradas"]:
            dot.append(f"{u}|{d['grupo']}|{e['modo']}|{e['usuario']}|{e['grupo']}|{e['ruta']}")
    comp = "\n".join(f"CAT|{c}|completo={int(v['completo'])}|truncado={int(v['truncado'])}|total={v['total']}|motivo={'; '.join(v['inc'])}"
                     for c, v in est.items())
    comp += "".join(f"\nAJENO|{c}|{x}" for c, v in est.items() for x in dict.fromkeys(v["ajenos"]))
    def mk(cid, lineas):
        return Registro(id=cid, comando=cmd, stdout="\n".join(lineas), rc=0, sudo=True, fecha=fecha)
    return {"fs.world_writable": mk("fs.world_writable", ww), "fs.sin_dueno": mk("fs.sin_dueno", nu),
            "fs.suid_sgid": mk("fs.suid_sgid", sg13), "fsd.privilegiados_6336": mk("fsd.privilegiados_6336", sg6),
            "usr.dotfiles_completo": mk("usr.dotfiles_completo", dot), "fsd.completitud": mk("fsd.completitud", comp.splitlines()),
            "fsd.instrumentacion": mk("fsd.instrumentacion", [] if par.get("instr_fs") is None else
                                      [f"INSTR_FS|{'|'.join(f'{k}={v}' for k, v in par['instr_fs'].items())}"])}


def completitud(ctx_lineas: List[str], cat: str) -> Optional[Dict]:
    """Lectura de fsd.completitud para una categoría (ww, nu, sg13, sg6, dot), o None si no hay evidencia FS."""
    for l in ctx_lineas:
        p = l.split("|", 5)
        if p[0] == "CAT" and len(p) == 6 and p[1] == cat:
            kv = dict(x.split("=", 1) for x in p[2:5])
            return {"completo": kv.get("completo") == "1", "truncado": kv.get("truncado") == "1", "total": int(kv.get("total", 0)),
                    "motivo": p[5].split("=", 1)[1] if "=" in p[5] else p[5],
                    "ajenos": [x.split("|", 2)[2] for x in ctx_lineas if x.startswith(f"AJENO|{cat}|")]}
    return None
