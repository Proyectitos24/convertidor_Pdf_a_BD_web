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

TOL_Y = 2.0
MOVE_PDFS = True  # True = mueve el PDF a la carpeta destino / False = solo copia

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
    RF626A: CODIGO ... CANTIDAD (B/U) UNIDADES
    (y soporta códigos de 2 dígitos)
    """
    tokens = clean_tokens(tokens)
    if not tokens:
        return None

    # Código: normalmente es el primer token
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

    # patrón cantidad + (B/U) + unidades
    q_idx = None
    for i in range(code_idx + 2, len(tokens) - 1):
        if tokens[i] in ("B", "U") and tokens[i - 1].isdigit() and tokens[i + 1].isdigit():
            q_idx = i - 1
            fmt_idx = i
            break
    if q_idx is None:
        return None

    cantidad = int(tokens[q_idx])
    descripcion = " ".join(tokens[code_idx + 1 : q_idx]).strip()
    if not descripcion:
        return None

    return codigo, descripcion, cantidad

def extract_items_rf626a(page):
    words = page.get_text("words")
    width = float(page.rect.width)
    split_x = width / 2.0

    line_groups = defaultdict(list)
    for x0, y0, x1, y1, w, b, l, wn in words:
        key = round(y0 / TOL_Y) * TOL_Y
        line_groups[key].append((x0, w))

    items = []
    for ykey in sorted(line_groups.keys()):
        pairs = sorted(line_groups[ykey], key=lambda p: p[0])
        left = [w for x, w in pairs if x < split_x]
        right = [w for x, w in pairs if x >= split_x]

        for side in (left, right):
            parsed = parse_side_rf626a(side)
            if parsed:
                items.append(parsed)
    return items

# ---------- DB ----------
def ensure_schema(cur):
    cur.execute("CREATE TABLE IF NOT EXISTS Etiqueta (Etiqueta TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS Codigo (Codigo TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS Descripcion (Descripcion TEXT PRIMARY KEY)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Linea (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Etiqueta TEXT NOT NULL,
            Codigo TEXT NOT NULL,
            Descripcion TEXT NOT NULL,
            Cantidad INTEGER NOT NULL,
            Falta INTEGER DEFAULT 0
        )
    """)

def write_db(etiqueta, acc, out_path: Path):
    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    ensure_schema(cur)

    cur.execute("DELETE FROM Linea")
    cur.execute("DELETE FROM Etiqueta")
    cur.execute("DELETE FROM Codigo")
    cur.execute("DELETE FROM Descripcion")

    cur.execute("INSERT INTO Etiqueta (Etiqueta) VALUES (?)", (etiqueta,))

    for (codigo, descripcion), cantidad in acc.items():
        cur.execute("INSERT OR IGNORE INTO Codigo (Codigo) VALUES (?)", (codigo,))
        cur.execute("INSERT OR IGNORE INTO Descripcion (Descripcion) VALUES (?)", (descripcion,))
        cur.execute(
            "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta) VALUES (?, ?, ?, ?, 0)",
            (etiqueta, codigo, descripcion, cantidad),
        )

    conn.commit()
    conn.close()

# ---------- process one PDF ----------
def process_pdf(pdf_path: Path, out_root: Path):
    doc = fitz.open(str(pdf_path))
    first_text = doc[0].get_text("text") or ""

    tienda = get_tienda(first_text)
    fecha = get_fecha(first_text)

    tienda_folder = out_root / f"Tienda_{tienda}"
    day_folder = tienda_folder / fecha
    pdfs_folder = day_folder / "pdfs"
    db_folder = day_folder / "db"
    pdfs_folder.mkdir(parents=True, exist_ok=True)
    db_folder.mkdir(parents=True, exist_ok=True)

    # por_etiqueta -> (codigo,descripcion)->cantidad
    por_etiqueta = defaultdict(lambda: defaultdict(int))

    for page in doc:
        page_text = page.get_text("text") or ""
        etq = get_etiqueta(page_text)

        for codigo, descripcion, cantidad in extract_items_rf626a(page):
            por_etiqueta[etq][(codigo, descripcion)] += cantidad

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
        write_db(etq, acc, out_db)
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
