# import re
# import sqlite3
# import fitz  # PyMuPDF
# from collections import defaultdict

# PDF_PATH = "central.pdf"
# OUT_DIR = "."          # carpeta de salida
# TOL_Y = 2.0            # tolerancia para agrupar palabras por línea

# def get_etiqueta(page) -> str:
#     txt = page.get_text("text") or ""
#     m = re.search(r"ETIQUETA.*?(\d{5,})", txt)
#     return m.group(1) if m else "00000000000"

# def clean_tokens(tokens):
#     out = []
#     prev = None
#     for t in tokens:
#         if "..." in t or re.fullmatch(r"\.+", t):
#             continue
#         if t == prev:
#             continue
#         out.append(t)
#         prev = t
#     return out

# def parse_side(tokens):
#     tokens = clean_tokens(tokens)

#     code_idx = None
#     for i, t in enumerate(tokens):
#         if t.isdigit() and 2 <= len(t) <= 12:
#             code_idx = i
#             break
#     if code_idx is None:
#         return None

#     codigo = tokens[code_idx]

#     if code_idx + 1 < len(tokens) and tokens[code_idx + 1] == codigo:
#         tokens.pop(code_idx + 1)

#     q_idx = None
#     for i in range(code_idx + 1, len(tokens) - 1):
#         if tokens[i] in ("B", "U") and tokens[i - 1].isdigit() and tokens[i + 1].isdigit():
#             q_idx = i - 1
#             break
#     if q_idx is None:
#         return None

#     cantidad = int(tokens[q_idx])
#     descripcion = " ".join(tokens[code_idx + 1 : q_idx]).strip()
#     if not descripcion:
#         return None

#     return codigo, descripcion, cantidad

# def extract_items_from_page(page):
#     words = page.get_text("words")
#     width = float(page.rect.width)
#     split_x = width / 2.0

#     line_groups = defaultdict(list)
#     for x0, y0, x1, y1, w, b, l, wn in words:
#         key = round(y0 / TOL_Y) * TOL_Y
#         line_groups[key].append((x0, w))

#     items = []
#     for ykey in sorted(line_groups.keys()):
#         pairs = sorted(line_groups[ykey], key=lambda p: p[0])
#         left = [w for x, w in pairs if x < split_x]
#         right = [w for x, w in pairs if x >= split_x]

#         for side in (left, right):
#             parsed = parse_side(side)
#             if parsed:
#                 items.append(parsed)

#     return items

# def ensure_schema(cur):
#     cur.execute("CREATE TABLE IF NOT EXISTS Etiqueta (Etiqueta TEXT PRIMARY KEY)")
#     cur.execute("CREATE TABLE IF NOT EXISTS Codigo (Codigo TEXT PRIMARY KEY)")
#     cur.execute("CREATE TABLE IF NOT EXISTS Descripcion (Descripcion TEXT PRIMARY KEY)")
#     cur.execute("""
#         CREATE TABLE IF NOT EXISTS Linea (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             Etiqueta TEXT NOT NULL,
#             Codigo TEXT NOT NULL,
#             Descripcion TEXT NOT NULL,
#             Cantidad INTEGER NOT NULL,
#             Falta INTEGER DEFAULT 0
#         )
#     """)

# def write_db(etiqueta, acc, out_path):
#     conn = sqlite3.connect(out_path)
#     cur = conn.cursor()
#     ensure_schema(cur)

#     cur.execute("DELETE FROM Linea")
#     cur.execute("DELETE FROM Etiqueta")
#     cur.execute("DELETE FROM Codigo")
#     cur.execute("DELETE FROM Descripcion")

#     cur.execute("INSERT INTO Etiqueta (Etiqueta) VALUES (?)", (etiqueta,))

#     for (codigo, descripcion), cantidad in acc.items():
#         cur.execute("INSERT OR IGNORE INTO Codigo (Codigo) VALUES (?)", (codigo,))
#         cur.execute("INSERT OR IGNORE INTO Descripcion (Descripcion) VALUES (?)", (descripcion,))
#         cur.execute(
#             "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta) VALUES (?, ?, ?, ?, 0)",
#             (etiqueta, codigo, descripcion, cantidad),
#         )

#     conn.commit()
#     conn.close()

# def main():
#     doc = fitz.open(PDF_PATH)

#     # por etiqueta -> acumulado (codigo,descripcion)->cantidad
#     por_etiqueta = defaultdict(lambda: defaultdict(int))

#     for page in doc:
#         etq = get_etiqueta(page)
#         for codigo, descripcion, cantidad in extract_items_from_page(page):
#             por_etiqueta[etq][(codigo, descripcion)] += cantidad

#     for etq, acc in por_etiqueta.items():
#         out_db = f"{OUT_DIR}/packinglist_etq_{etq}.db"
#         write_db(etq, acc, out_db)
#         print(f"✅ Generado: {out_db} ({len(acc)} productos)")

# if __name__ == "__main__":
#     main()

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF

from services import grupo_packinglist as grp
from services import tipo_medida as tm

TOL_Y = 2.0
MOVE_PDFS = True  # True = mueve el PDF a la carpeta destino / False = solo copia

PALLET_ENTERO_UN_BULTO = {
    "26995",
    "26996",
    "263592",
    "608",
    "607",
    "504",
}

PALLET_ENTERO_CONDICIONAL = {
    "14752",
}

# ---------- UI (Windows) ----------
def pick_files_and_folder():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    pdfs = filedialog.askopenfilenames(
        title="Selecciona uno o varios PDF (RF626A con etiqueta)",
        filetypes=[("PDF files", "*.pdf")],
    )
    if not pdfs:
        return [], None

    out_dir = filedialog.askdirectory(title="Selecciona carpeta DESTINO")
    if not out_dir:
        return [], None

    return list(pdfs), Path(out_dir)

# ---------- header ----------
def get_tienda(text: str) -> str:
    m = re.search(r"TIENDA/CONCESION\.\.\:\s*(\d{5})", text)
    return m.group(1) if m else "00000"

def get_fecha(text: str) -> str:
    m = re.search(r"Fecha\s*\.\.\:\s*(\d{1,2})/(\d{1,2})/(\d{2})", text)
    if not m:
        return datetime.now().strftime("%Y-%m-%d")
    d, mo, yy = map(int, m.groups())
    return datetime(2000 + yy, mo, d).strftime("%Y-%m-%d")

def get_etiqueta(text: str) -> str:
    m = re.search(r"ETIQUETA.*?(\d{5,})", text)
    return m.group(1) if m else "00000000000"

# ---------- parsing ----------
def clean_tokens(tokens):
    out, prev = [], None
    for t in tokens:
        if "..." in t or re.fullmatch(r"\.+", t):
            continue
        if t == prev:
            continue
        out.append(t)
        prev = t
    return out

def parse_side_rf626a(tokens):
    """
    Caso normal RF626A:
      B 6 296004 PAPEL MULTIUSOS 3CAP LALLA 1
      -> formato normal con cantidad + B/U + unidades

    Caso especial palet entero:
      S 360 608 LECHE ENTERA DIALA 1 L 60
      -> código 608, descripción 'LECHE ENTERA DIALA 1 L', cantidad real 60
      pero para ciertos códigos lo guardamos como 1 bulto.
    """
    tokens = clean_tokens(tokens)
    if not tokens:
        return None

    # ---- CASO ESPECIAL: palet entero / semipalet ----
    # Ejemplo:
    # S 360 608 LECHE ENTERA DIALA 1 L 60
    if (
        len(tokens) >= 5
        and tokens[0] in ("S", "P")
        and tokens[1].isdigit()
        and tokens[2].isdigit()
        and tokens[-1].isdigit()
    ):
        codigo = tokens[2]
        descripcion = " ".join(tokens[3:-1]).strip()
        cantidad_real = int(tokens[-1])

        if not descripcion:
            return None

        if codigo in PALLET_ENTERO_UN_BULTO or codigo in PALLET_ENTERO_CONDICIONAL:
            return codigo, descripcion, 1

        # si viniera otro código especial no contemplado,
        # de momento conserva la cantidad real
        return codigo, descripcion, cantidad_real

    # ---- CASO NORMAL ----
    code_idx = None
    if tokens[0].isdigit() and 2 <= len(tokens[0]) <= 12:
        code_idx = 0
    else:
        for i, t in enumerate(tokens):
            if t.isdigit() and 2 <= len(t) <= 12:
                code_idx = i
                break
    if code_idx is None:
        return None

    codigo = tokens[code_idx]

    q_idx = None
    for i in range(code_idx + 2, len(tokens) - 1):
        if tokens[i] in ("B", "U") and tokens[i - 1].isdigit() and tokens[i + 1].isdigit():
            q_idx = i - 1
            break
    if q_idx is None:
        return None

    cantidad = int(tokens[q_idx])
    descripcion = " ".join(tokens[code_idx + 1 : q_idx]).strip()
    if not descripcion:
        return None

    return codigo, descripcion, cantidad

def extract_items_rf626a(page):
    """
    Devuelve [(codigo, descripcion, cantidad, tipo_medida, problema)].
    La lectura de código/descripción/cantidad no cambia (parse_side_rf626a);
    tipo_medida sale del campo Formato de la misma línea y mitad de la
    página (services/tipo_medida.py). problema es None si es válida.
    Una línea bajo la cabecera que el parser no reconoce pero que tiene
    un valor en Formato también se devuelve, solo con su problema.
    """
    words = page.get_text("words")
    width = float(page.rect.width)
    split_x = width / 2.0
    rangos_formato = tm.columnas_formato(words, split_x)
    rangos_cantidad = tm.columnas_cantidad(words, split_x)
    y_cabecera = tm.y_cabecera_formato(words)

    line_groups = defaultdict(list)
    for x0, y0, x1, y1, w, b, l, wn in words:
        key = round(y0 / TOL_Y) * TOL_Y
        line_groups[key].append((x0, x1, w))

    items = []
    for ykey in sorted(line_groups.keys()):
        pairs = sorted(line_groups[ykey], key=lambda p: p[0])
        left = [p for p in pairs if p[0] < split_x]
        right = [p for p in pairs if p[0] >= split_x]

        for lado, side in (("izq", left), ("der", right)):
            parsed = parse_side_rf626a([w for _x0, _x1, w in side])
            if parsed:
                tipo, problema = tm.tipo_de_linea(side, rangos_formato.get(lado))
                items.append((*parsed, tipo, problema))
            elif y_cabecera is not None and ykey > y_cabecera:
                # Línea bajo la cabecera que el parser no reconoce pero
                # con un valor en Formato: se informa, no se descarta.
                problema = tm.problema_linea_no_reconocida(
                    side, rangos_formato.get(lado), rangos_cantidad.get(lado)
                )
                if problema:
                    codigo = next((w for _x0, _x1, w in side if w.isdigit()), side[0][2])
                    items.append((codigo, None, None, None, problema))
    return items

# ---------- DB ----------
def ensure_schema(cur):
    tm.ensure_schema(cur)

def write_db(etiqueta, acc, out_path: Path, grupo, grupo_fuente, formato_pdf):
    """
    acc: {(codigo, descripcion, tipo_medida): cantidad}. Si el destino ya
    existe con el esquema antiguo (sin TipoMedida) se lanza
    tm.EsquemaIncompatibleError sin modificarlo.
    """
    invalidos = [codigo for (codigo, _d, tipo) in acc if tipo not in tm.TIPOS_MEDIDA]
    if invalidos:
        raise ValueError(f"TipoMedida inválido para: {', '.join(invalidos)}")
    tm.comprobar_destino(out_path)
    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    ensure_schema(cur)

    cur.execute("DELETE FROM Linea")
    cur.execute("DELETE FROM Etiqueta")
    cur.execute("DELETE FROM Codigo")
    cur.execute("DELETE FROM Descripcion")

    cur.execute("INSERT INTO Etiqueta (Etiqueta) VALUES (?)", (etiqueta,))

    for (codigo, descripcion, tipo), cantidad in acc.items():
        cur.execute("INSERT OR IGNORE INTO Codigo (Codigo) VALUES (?)", (codigo,))
        cur.execute("INSERT OR IGNORE INTO Descripcion (Descripcion) VALUES (?)", (descripcion,))
        cur.execute(
            "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) VALUES (?, ?, ?, ?, 0, ?)",
            (etiqueta, codigo, descripcion, cantidad, tipo),
        )

    grp.write_metadata(cur, grupo, grupo_fuente, formato_pdf)

    conn.commit()
    conn.close()

# ---------- process one PDF ----------
def process_pdf(pdf_path: Path, out_root: Path):
    doc = fitz.open(str(pdf_path))
    first_text = doc[0].get_text("text") or ""

    tienda = get_tienda(first_text)
    fecha = get_fecha(first_text)
    formato_pdf = grp.detectar_formato_pdf(first_text, por_defecto="RF626A")

    tienda_folder = out_root / f"Tienda_{tienda}"
    day_folder = tienda_folder / fecha
    pdfs_folder = day_folder / "pdfs"
    db_folder = day_folder / "db"
    pdfs_folder.mkdir(parents=True, exist_ok=True)
    db_folder.mkdir(parents=True, exist_ok=True)

    # por_etiqueta -> (codigo,descripcion,tipo_medida)->cantidad
    # (bultos y unidades de un mismo artículo nunca se suman entre sí)
    por_etiqueta = defaultdict(lambda: defaultdict(int))
    problemas_medida = []
    areas_por_etiqueta = defaultdict(list)

    for page in doc:
        page_text = page.get_text("text") or ""
        etq = get_etiqueta(page_text)

        for codigo, descripcion, cantidad, tipo, problema in extract_items_rf626a(page):
            if problema:
                problemas_medida.append(f"Página {page.number + 1}, código {codigo}: {problema}")
                continue
            por_etiqueta[etq][(codigo, descripcion, tipo)] += cantidad

        etq_real = grp.extraer_etiqueta_real(page_text)
        if etq_real is not None:
            area = grp.extraer_area_pagina(page_text)
            if area:
                areas_por_etiqueta[etq_real].append(area)

    # Se valida TODO el documento antes de mover el PDF o escribir
    # cualquier .db: nunca queda un resultado parcial.
    if problemas_medida:
        doc.close()
        raise tm.MedidaError(problemas_medida)

    # Un .db ya existente con el esquema antiguo nunca se modifica: se
    # comprueba ANTES de mover el PDF para no dejar el lote a medias.
    try:
        for etq in por_etiqueta:
            tm.comprobar_destino(db_folder / f"packinglist_{tienda}_{fecha}_etq_{etq}.db")
    except tm.EsquemaIncompatibleError:
        doc.close()
        raise

    # mover/copy PDF
    dest_pdf = pdfs_folder / pdf_path.name
    if pdf_path.resolve() != dest_pdf.resolve():
        try:
            if MOVE_PDFS:
                pdf_path.replace(dest_pdf)
            else:
                import shutil
                shutil.copy2(pdf_path, dest_pdf)
        except Exception:
            import shutil
            shutil.copy2(pdf_path, dest_pdf)

    # escribir DB(s)
    out_dbs = []
    for etq, acc in por_etiqueta.items():
        out_db = db_folder / f"packinglist_{tienda}_{fecha}_etq_{etq}.db"
        grupo, grupo_fuente = grp.resolver_grupo(areas_por_etiqueta.get(etq, []))
        write_db(etq, acc, out_db, grupo, grupo_fuente, formato_pdf)
        out_dbs.append(out_db)

    return tienda, fecha, dest_pdf, out_dbs

def main():
    pdfs, out_root = pick_files_and_folder()
    if not pdfs or out_root is None:
        print("Cancelado.")
        return

    print(f"\n📁 DESTINO: {out_root}\n")

    for p in pdfs:
        tienda, fecha, saved_pdf, out_dbs = process_pdf(Path(p), out_root)
        print(f"✅ {saved_pdf.name} -> Tienda_{tienda}/{fecha}")
        for db in out_dbs:
            print(f"   🗃️ {db.name}")
        print()

    print("Listo.")

if __name__ == "__main__":
    main()
