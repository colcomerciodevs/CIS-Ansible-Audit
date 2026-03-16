#!/usr/bin/env python3
import json, argparse, pathlib
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

GREEN = "C6EFCE"   # Light green
ORANGE = "FCE4D6"  # Light orange
BORDER_GRAY = "D9D9D9"

def autosize(ws):
    widths = {}
    for row in ws.iter_rows(values_only=True):
        for idx, val in enumerate(row, start=1):
            l = len(str(val)) if val is not None else 0
            widths[idx] = max(widths.get(idx, 10), min(l + 2, 80))
    for idx, width in widths.items():
        ws.column_dimensions[get_column_letter(idx)].width = width

def build_report(json_path, output_path):
    data = json.loads(pathlib.Path(json_path).read_text(encoding="utf-8"))
    host = data.get("host", "")
    ip = data.get("ip", "")
    results = data.get("results", [])

    wb = Workbook()
    ws = wb.active
    ws.title = "Auditoría"

    headers = ["HOST", "IP", "CONTROL", "DESCRIPCIÓN", "COMANDO DE VALIDACIÓN", "CUMPLE", "RC", "STDOUT", "STDERR"]
    ws.append(headers)

    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(vertical="top")

    for r in results:
        # Fallbacks para nombres de campos
        control = r.get("control_id") or r.get("id") or r.get("CONTROL") or ""
        descripcion = (
            r.get("control_name")
            or r.get("name")
            or r.get("DESCRIPCIÓN")
            or r.get("DESCRIPCION")
            or ""
        )
        command = r.get("command") or r.get("COMANDO_DE_VALIDACION") or ""

        # passed: usar booleano si viene, si no deducir desde "CUMPLE"
        if "passed" in r:
            passed = bool(r.get("passed"))
        else:
            cumple_txt = (r.get("CUMPLE") or "").strip().lower()
            passed = True if cumple_txt == "cumple" else False

        rc = r.get("rc")
        stdout = (r.get("stdout") or "")[:32760]
        stderr = (r.get("stderr") or "")[:32760]

        row = [
            host,
            ip,
            control,
            descripcion,
            command,
            "CUMPLE" if passed else "NO CUMPLE",
            rc,
            stdout,
            stderr,
        ]
        ws.append(row)

        # Pintar estado
        status_cell = ws.cell(row=ws.max_row, column=6)
        status_cell.fill = PatternFill("solid", fgColor=(GREEN if passed else ORANGE))

    # Formato celdas
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=9):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    autosize(ws)
    pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crear XLSX de auditoría por host")
    parser.add_argument("--input", required=True, help="JSON con resultados")
    parser.add_argument("--output", required=True, help="Ruta del XLSX a generar")
    args = parser.parse_args()
    build_report(args.input, args.output)
