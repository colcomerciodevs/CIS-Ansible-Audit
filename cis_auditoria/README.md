# Auditoría de controles (Oracle/RHEL 8-9) con Excel por host

Este paquete contiene un *playbook* de Ansible que ejecuta los **comandos de validación** que ya tienes en tu matriz (columna Oracle/RHEL 8-9) y genera un **reporte en Excel por host**, con el **nombre del archivo usando la IP**.

## Estructura
```
ansible_auditoria_excel/
├── site.yml
├── group_vars/
│   └── all/
│       └── audit_controls.yml   # <<< Rellena con tus comandos de validación
└── scripts/
    └── write_excel_report.py    # Genera el XLSX, requiere openpyxl
```

## Requisitos
- Ansible (control node)
- Python 3 con `openpyxl` instalado en el **control node** (localhost):
  ```bash
  pip install openpyxl
  ```

## Cómo usar
1. **Edita** `group_vars/all/audit_controls.yml` y coloca los **comandos exactos** de tu matriz para cada control.  
   Puedes usar `pass_if_regex`/`fail_if_regex` para definir la lógica de cumplimiento por patrón (regex).  
   Si no defines regex, el control **se considera CUMPLE si el comando termina con rc==0**.

2. Prepara tu inventario (ejemplo `hosts.ini`):
   ```ini
   [servidores]
   10.1.2.3 ansible_user=ec2-user ansible_ssh_private_key_file=~/.ssh/key.pem
   10.1.2.4 ansible_user=opc
   ```

3. Ejecuta el playbook:
   ```bash
   ansible-playbook -i hosts.ini site.yml
   ```

4. Al finalizar, encontrarás un archivo por host dentro de `./_audit_reports/` con nombre:
   ```
   Audit_<IP>.xlsx
   ```

## ¿Cómo resalta colores?
- **Verde** si el control **CUMPLE**.
- **Naranja** si **NO CUMPLE**.

## Consejos
- Si un control aplica solo a RHEL/OL 8-9, agrega una condición en el ítem:
  ```yaml
  when: ansible_facts.os_family == "RedHat" and ansible_facts.distribution_major_version in ["8", "9"]
  ```
- Si tus comandos devuelven salida extensa, quedará recortada a 32K por celda (límite seguro para Excel).

## Personalización
- Puedes añadir más columnas fácilmente editando `scripts/write_excel_report.py`.
- Si prefieres un único Excel consolidado, avísame y te lo adapto.
