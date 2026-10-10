import re
import sqlite3
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import fitz  # PyMuPDF

from services import grupo_packinglist as grp
from services import tipo_medida as tm

# ==== CONFIG ====
TOL_Y = 2.0
TIENDAS_PREF = {"14140", "14102", "14017", "14196", "14043"}  # solo para “orden”, no limita

# ---------- selectors (Windows) ----------
def pick_files_and_folder():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    pdfs = filedialog.askopenfilenames(
        title="Selecciona uno o varios PDF (cajas azules RF625A)",
        filetypes=[("PDF files", "*.pdf")],
    )
    if not pdfs:
        return [], None

    out_dir = filedialog.askdirectory(title="Selecciona carpeta DESTINO")
    if not out_dir:
        return [], None

    return list(pdfs), Path(out_dir)

# ---------- header parsers ----------
def get_tienda(text: str) -> str:
    m = re.search(r"TIENDA/CONCESION\.\.\:\s*(\d{5})", text)
    return m.group(1) if m else "00000"

def get_fecha(text: str) -> str:
    m = re.search(r"Fecha\s*\.\.\:\s*(\d{1,2})/(\d{1,2})/(\d{2})", text)
    if not m:
        return datetime.now().strftime("%Y-%m-%d")
    d, mo, yy = map(int, m.groups())
    return datetime(2000 + yy, mo, d).strftime("%Y-%m-%d")

def get_albaran(text: str) -> str:
    # Ej: "NUMERO DE ALBARAN ..........: 0- 610268"
    m = re.search(r"NUMERO DE ALBARAN\s*\.*:\s*([0-9]+\s*-\s*[0-9]+)", text)
    if not m:
        return "0-000000"
    return re.sub(r"\s+", "", m.group(1))  # "0-610268"

def is_rf625a(text: str) -> bool:
    # ayuda para no mezclar con otros reportes
    return ("RF625A" in text) or ("LISTADO CAJAS" in text.upper())

# ---------- token parsing (RF625A) ----------
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

def parse_side_rf625a(tokens):
    """
    RF625A: CODIGO ... CANTIDAD FORMATO (U/B)
    Ej: 297243 ... 1 U
    """
    tokens = clean_tokens(tokens)

    code_idx = None
    for i, t in enumerate(tokens):
        if t.isdigit() and 3 <= len(t) <= 12:
            code_idx = i
            break
    if code_idx is None:
        return None

    codigo = tokens[code_idx]

    fmt_idx = None
    for i in range(code_idx + 2, len(tokens)):
        if tokens[i] in ("U", "B") and tokens[i - 1].isdigit():
            fmt_idx = i
            break
    if fmt_idx is None:
        return None

    cantidad = int(tokens[fmt_idx - 1])
    descripcion = " ".join(tokens[code_idx + 1 : fmt_idx - 1]).strip()
    if not descripcion:
        return None

    return codigo, descripcion, cantidad

def extract_items_rf625a(page):
    """
    Devuelve [(codigo, descripcion, cantidad, tipo_medida, problema)].
    La lectura de código/descripción/cantidad no cambia (parse_side_rf625a);
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
            parsed = parse_side_rf625a([w for _x0, _x1, w in side])
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

# ---------- DB schema ----------
def ensure_schema(cur):
    tm.ensure_schema(cur)

def write_db(etiqueta, acc, out_db_path: Path, grupo, grupo_fuente, formato_pdf):
    """
    acc: {(codigo, descripcion, tipo_medida): cantidad}. Si el destino ya
    existe con el esquema antiguo (sin TipoMedida) se lanza
    tm.EsquemaIncompatibleError sin modificarlo.
    """
    invalidos = [codigo for (codigo, _d, tipo) in acc if tipo not in tm.TIPOS_MEDIDA]
    if invalidos:
        raise ValueError(f"TipoMedida inválido para: {', '.join(invalidos)}")
    tm.comprobar_destino(out_db_path)
    conn = sqlite3.connect(out_db_path)
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

# ---------- process one pdf ----------
def process_pdf(pdf_path: Path, out_root: Path):
    doc = fitz.open(str(pdf_path))
    first_text = doc[0].get_text("text") or ""

    if not is_rf625a(first_text):
        return {"ok": False, "reason": "No parece RF625A / cajas azules", "pdf": pdf_path.name}

    tienda = get_tienda(first_text)
    fecha = get_fecha(first_text)
    albaran = get_albaran(first_text)

    # “Etiqueta” interna para que tu app lo lea igual
    etiqueta = f"{tienda}_{albaran}"

    tienda_folder = out_root / f"Tienda_{tienda}"
    day_folder = tienda_folder / fecha
    pdfs_folder = day_folder / "pdfs"
    db_folder = day_folder / "db"
    pdfs_folder.mkdir(parents=True, exist_ok=True)
    db_folder.mkdir(parents=True, exist_ok=True)

    # (codigo, descripcion, tipo_medida) -> cantidad: bultos y unidades
    # de un mismo artículo nunca se suman entre sí.
    acc = defaultdict(int)
    areas_vistas = []
    problemas_medida = []
    for page in doc:
        for codigo, descripcion, cantidad, tipo, problema in extract_items_rf625a(page):
            if problema:
                problemas_medida.append(f"Página {page.number + 1}, código {codigo}: {problema}")
                continue
            acc[(codigo, descripcion, tipo)] += cantidad

        area = grp.extraer_area_pagina(page.get_text("text") or "")
        if area:
            areas_vistas.append(area)

    # Se valida TODO el documento antes de mover el PDF o escribir el .db.
    if problemas_medida:
        doc.close()
        raise tm.MedidaError(problemas_medida)

    grupo, grupo_fuente = grp.resolver_grupo(areas_vistas)

    # Un .db ya existente con el esquema antiguo nunca se modifica: se
    # comprueba ANTES de mover el PDF.
    out_db = db_folder / f"cajas_azules_{tienda}_{fecha}_alb_{albaran}.db"
    try:
        tm.comprobar_destino(out_db)
    except tm.EsquemaIncompatibleError:
        doc.close()
        raise

    # mover PDF a destino
    dest_pdf = pdfs_folder / pdf_path.name
    if pdf_path.resolve() != dest_pdf.resolve():
        try:
            pdf_path.replace(dest_pdf)
        except Exception:
            import shutil
            shutil.copy2(pdf_path, dest_pdf)

    # DB con fecha + albarán (y tienda)
    write_db(etiqueta, acc, out_db, grupo, grupo_fuente, "RF625A")

    return {
        "ok": True,
        "tienda": tienda,
        "fecha": fecha,
        "albaran": albaran,
        "pdf_saved": str(dest_pdf),
        "db_saved": str(out_db),
        "productos": len(acc),
    }

def main():
    pdfs, out_root = pick_files_and_folder()
    if not pdfs or out_root is None:
        print("Cancelado.")
        return

    print(f"\n📁 DESTINO: {out_root}\n")

    for p in pdfs:
        r = process_pdf(Path(p), out_root)
        if not r["ok"]:
            print(f"⚠️  {r['pdf']} -> {r['reason']}")
            continue

        print(f"✅ {Path(r['pdf_saved']).name}  ->  Tienda_{r['tienda']}/{r['fecha']}")
        print(f"   🗃️ {Path(r['db_saved']).name}  ({r['productos']} productos)")
        print()

    print("Listo.")

if __name__ == "__main__":
    main()


