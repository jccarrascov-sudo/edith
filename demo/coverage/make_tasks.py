"""The 20 student tasks of the coverage mini-benchmark (scripts/coverage_bench.py).

Each task: a sheet, a Word template (or the Northwind desktop form), a request for one row (run 1,
free mode) and another row (run 2, the induced recipe), and the values a correct result must hold.
Everything is fictitious and made here; `make(folder)` writes the files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import docx
import openpyxl
from docx.shared import Pt

STUDENTS = [("Lucía Paredes", "20231045", "Ingeniería Informática", 17, 15, 18),
            ("Mateo Quispe", "20221877", "Arquitectura", 14, 16, 13),
            ("Valeria Ñahui", "20240312", "Psicología", 19, 18, 20),
            ("Andrés Muñoz", "20219904", "Derecho", 12, 11, 15),
            ("Camila Rojas", "20235521", "Medicina", 16, 17, 16),
            ("José Álvarez", "20228843", "Economía", 13, 14, 12)]
EVENTS = [("Taller de Python", "12/11/2026", "Aula 304", "4 horas"), ("Feria de Ciencias", "20/11/2026", "Auditorio", "6 horas"),
          ("Hackatón PUCP", "05/12/2026", "Laboratorio 2", "24 horas"), ("Congreso de Psicología", "28/11/2026", "Sala B", "8 horas")]
PAYMENTS = [("Lucía Paredes", "Matrícula 2026-2", 850.0, "03/08/2026"), ("Mateo Quispe", "Pensión octubre", 1240.5, "05/10/2026"),
            ("Valeria Ñahui", "Carné universitario", 35.0, "10/09/2026"), ("Andrés Muñoz", "Pensión noviembre", 1240.5, "04/11/2026")]


def _doc(path: Path, title: str, lines: list[str], bold_words: list[str] | None = None) -> None:
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(11)
    r = d.add_paragraph().add_run(title)
    r.bold = True
    r.font.size = Pt(15)
    for line in lines:
        p = d.add_paragraph()
        parts = [line]
        for w in bold_words or []:
            if w in line:
                a, b = line.split(w, 1)
                parts = [a, w, b]
                break
        for i, part in enumerate(parts):
            run = p.add_run(part)
            if len(parts) == 3 and i == 1:
                run.bold = True
    d.save(str(path))


def _sheet(path: Path, header: list[str], rows: list[tuple]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(list(r))
    wb.save(str(path))


DOC_TASKS = [
    # (id, sheet, template, title, template lines, request (row 1), row 1, row 2, expected fields)
    ("constancia", "alumnos.xlsx", "Constancia de notas.docx", "CONSTANCIA DE NOTAS",
     ["Se deja constancia que NOMBRE, con código CODIGO, de la carrera de CARRERA,", "obtuvo un promedio de PROMEDIO.",
      "Lima, noviembre de 2026."], "haz la constancia de notas de Lucía Paredes con la plantilla", 0, 2, ["Nombre", "Código"]),
    ("certificado", "eventos.xlsx", "Certificado.docx", "CERTIFICADO",
     ["Se certifica la participación en EVENTO,", "realizado el FECHA en LUGAR, con una duración de DURACION."],
     "crea el certificado del Taller de Python usando la plantilla", 0, 2, ["Evento", "Lugar"]),
    ("recibo", "pagos.xlsx", "Recibo.docx", "RECIBO DE PAGO",
     ["Recibimos de ALUMNO la suma de S/ MONTO", "por concepto de CONCEPTO, el FECHA."],
     "genera el recibo de pago de Mateo Quispe con la plantilla", 1, 3, ["Alumno", "Concepto"]),
    ("carta", "invoices.xlsx", "Payment reminder.docx", None, None,
     "prepara la carta de recordatorio de pago para Panadería Ñandú con la plantilla", 4, 12, ["Client", "Description"]),
    ("invitacion", "eventos.xlsx", "Invitación.docx", "INVITACIÓN",
     ["Te invitamos a EVENTO.", "Fecha: FECHA · Lugar: LUGAR."], "haz la invitación a la Feria de Ciencias con la plantilla", 1, 3,
     ["Evento", "Fecha"]),
    ("credencial", "alumnos.xlsx", "Credencial.docx", "CREDENCIAL DE ESTUDIANTE",
     ["Nombre: NOMBRE", "Código: CODIGO", "Carrera: CARRERA"], "crea la credencial de Valeria Ñahui con la plantilla", 2, 4,
     ["Nombre", "Carrera"]),
    ("justificacion", "alumnos.xlsx", "Justificación.docx", "SOLICITUD DE JUSTIFICACIÓN",
     ["Yo, NOMBRE, con código CODIGO, solicito se justifique mi inasistencia.", "Atentamente,"],
     "prepara la solicitud de justificación de Andrés Muñoz con la plantilla", 3, 5, ["Nombre", "Código"]),
    ("boleta", "alumnos.xlsx", "Boleta de notas.docx", "BOLETA DE NOTAS",
     ["Alumno: NOMBRE", "Parcial: PARCIAL · Final: FINAL · Prácticas: PRACTICAS"],
     "genera la boleta de notas de Camila Rojas con la plantilla", 4, 0, ["Nombre", "Parcial"]),
    ("constancia_pago", "pagos.xlsx", "Constancia de pago.docx", "CONSTANCIA DE PAGO",
     ["ALUMNO pagó CONCEPTO el FECHA.", "Monto: S/ MONTO"], "crea la constancia de pago de Valeria Ñahui con la plantilla", 2, 0,
     ["Alumno", "Concepto"]),
    ("diploma", "eventos.xlsx", "Diploma.docx", "DIPLOMA",
     ["Por su destacada participación en EVENTO (DURACION)."], "haz el diploma del Hackatón PUCP con la plantilla", 2, 0,
     ["Evento", "Duración"]),
    ("ficha", "alumnos.xlsx", "Ficha de matrícula.docx", "FICHA DE MATRÍCULA 2026-2",
     ["Estudiante: NOMBRE (CODIGO)", "Programa: CARRERA"], "prepara la ficha de matrícula de José Álvarez con la plantilla", 5, 1,
     ["Nombre", "Código"]),
    ("aviso", "pagos.xlsx", "Aviso de deuda.docx", "AVISO",
     ["Estimado(a) ALUMNO: tiene pendiente CONCEPTO por S/ MONTO."], "genera el aviso de deuda de Andrés Muñoz con la plantilla", 3, 1,
     ["Alumno", "Concepto"]),
    ("acta", "eventos.xlsx", "Acta.docx", "ACTA DE ACTIVIDAD",
     ["Actividad: EVENTO", "Lugar: LUGAR", "Fecha: FECHA"], "haz el acta del Congreso de Psicología con la plantilla", 3, 1,
     ["Evento", "Lugar"]),
    ("reconocimiento", "alumnos.xlsx", "Reconocimiento.docx", "RECONOCIMIENTO",
     ["Se reconoce a NOMBRE por su promedio de PROMEDIO en CARRERA."],
     "crea el reconocimiento de Valeria Ñahui con la plantilla", 2, 0, ["Nombre", "Carrera"]),
    ("comprobante", "pagos.xlsx", "Comprobante.docx", "COMPROBANTE",
     ["Concepto: CONCEPTO", "Alumno: ALUMNO", "Total: S/ MONTO"], "genera el comprobante de Lucía Paredes con la plantilla", 0, 2,
     ["Concepto", "Alumno"]),
    ("carta_practicas", "alumnos.xlsx", "Carta de presentación.docx", "CARTA DE PRESENTACIÓN",
     ["Presentamos a NOMBRE, estudiante de CARRERA con código CODIGO,", "para realizar sus prácticas preprofesionales."],
     "prepara la carta de presentación de Mateo Quispe con la plantilla", 1, 3, ["Nombre", "Carrera"]),
]

FORM_TASKS = [  # (id, request (row 1), row 1, row 2) on Northwind Desktop with invoices.xlsx
    ("northwind_1", "registra en Northwind la factura de Paper Moth Bakery del Excel", 0, 5),
    ("northwind_2", "ingresa en Northwind la factura de Café Señorial", 8, 13),
    ("northwind_3", "llena en Northwind la factura de Taller Ñuñoa", 21, 26),
    ("northwind_4", "registra en Northwind la factura de Librería Acuña", 12, 16),
]


def make(folder: Path) -> dict[str, Path]:
    import shutil

    folder.mkdir(parents=True, exist_ok=True)
    _sheet(folder / "alumnos.xlsx", ["Nombre", "Código", "Carrera", "Parcial", "Final", "Prácticas"],
           [(n, c, k, p, f, pr) for n, c, k, p, f, pr in STUDENTS])
    _sheet(folder / "eventos.xlsx", ["Evento", "Fecha", "Lugar", "Duración"], EVENTS)
    _sheet(folder / "pagos.xlsx", ["Alumno", "Concepto", "Monto", "Fecha"], PAYMENTS)
    shutil.copy2(Path(__file__).resolve().parents[1] / "data" / "invoices.xlsx", folder / "invoices.xlsx")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import make_letters

    make_letters.template(folder / "Payment reminder.docx")
    for _id, _sheet_name, template, title, lines, *_rest in DOC_TASKS:
        if title:
            _doc(folder / template, title, lines)
    return {"folder": folder}


if __name__ == "__main__":
    import sys

    print(make(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data"))
