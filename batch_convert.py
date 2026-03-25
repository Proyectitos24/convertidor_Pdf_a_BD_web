import re
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF

# Carpeta/tiendas esperadas (si viene otra tienda, igual se crea Tienda_<codigo>)
TIENDAS = {"14140", "14102", "14017", "14196", "14043"}

TOL_Y = 2.0  # tolerancia para agrupar palabras por línea

# ---------- extracción desde PDF ----------

def get_tienda(page_text: str) -> str:
    # Ej: "TIENDA/CONCESION..: 14196/00"
    m = re.search(r"TIENDA/CONCESION\.\.\:\s*(\d{5})", page_text)
    if m:
        return m.group(1)
    # fallback por si cambia el texto
    m = re.search(r"\bTIENDA\b.*?(\d{5})", page_text)
    return m.group(1) if m else "00000"

def get_fecha(page_text: str) -> str:
    # Ej: "Fecha ..: 9/01/26" -> 2026-01-09
    m = re.search(r"Fecha\s*\.\.\:\s*(\d{1,2})/(\d{1,2})/(\d{2})", page_text)
    if m:
        d, mo, yy = map(int, m.groups())
        year = 2000 + yy
        try:
            return datetime(year, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now().strftime("%Y-%m-%d")

def get_etiqueta(page_text: str) -> str:
    m = re.search(r"ETIQUETA.*?(\d{5,})", page_text)
    return m.group(1) if m else "00000000000"

def clean_tokens(tokens):
    out = []
    prev = None
    for t in tokens:
        if "..." in t or re.fullmatch(r"\.+", t):
            continue
        if t == prev:
            continue
        out.append(t)
        prev = t
    return out

def parse_side(tokens):
    tokens = clean_tokens(tokens)

    # primer código numérico
    code_idx = None
    for i, t in enumerate(tokens):
        if t.isdigit() and 3 <= len(t) <= 12:
            code_idx = i
            break
    if code_idx is None:
        return None

    codigo = tokens[code_idx]

    # quitar duplicado inmediato del código (a veces repite)
    if code_idx + 1 < len(tokens) and tokens[code_idx + 1] == codigo:
        tokens.pop(code_idx + 1)

    # buscar patrón: (cantidad) (B/U) (unidades)
    q_idx = None
    for i in range(code_idx + 1, len(tokens) - 1):
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

def extract_items_from_page(page):
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
            parsed = parse_side(side)
            if parsed:
                items.append(parsed)

    return items

# ---------- escritura DB ----------

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

def write_db(etiqueta, acc, out_path):
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

# ---------- batch ----------

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
        for codigo, descripcion, cantidad in extract_items_from_page(page):
            por_etiqueta[etq][(codigo, descripcion)] += cantidad

    # mover/copy PDF al folder
    dest_pdf = pdfs_folder / pdf_path.name
    if pdf_path.resolve() != dest_pdf.resolve():
        try:
            pdf_path.replace(dest_pdf)  # mueve
        except Exception:
            # si está bloqueado o sin permisos, copia
            import shutil
            shutil.copy2(pdf_path, dest_pdf)

    generados = []
    for etq, acc in por_etiqueta.items():
        db_name = f"packinglist_{tienda}_{fecha}_etq_{etq}.db"
        out_db = db_folder / db_name
        write_db(etq, acc, str(out_db))
        generados.append(out_db)

    return tienda, fecha, dest_pdf, generados

def pick_files_and_folder():
    # selector Windows (tkinter)
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    pdfs = filedialog.askopenfilenames(
        title="Selecciona uno o varios PDF",
        filetypes=[("PDF files", "*.pdf")],
    )
    if not pdfs:
        return [], None

    out_dir = filedialog.askdirectory(title="Selecciona carpeta destino")
    if not out_dir:
        return [], None

    return list(pdfs), Path(out_dir)

def main():
    pdfs, out_root = pick_files_and_folder()
    if not pdfs or out_root is None:
        print("Cancelado.")
        return

    print(f"\n📁 Destino: {out_root}\n")

    for p in pdfs:
        pdf_path = Path(p)
        tienda, fecha, dest_pdf, generados = process_pdf(pdf_path, out_root)
        print(f"✅ {dest_pdf.name}  ->  Tienda_{tienda}/{fecha}")
        for db in generados:
            print(f"   🗃️ {db.name}")
        print()

    print("Listo.")

if __name__ == "__main__":
    main()
