import pdfplumber

PDF_PATH = "albaran_pollo_260109_123335.pdf"

with pdfplumber.open(PDF_PATH) as pdf:
    page = pdf.pages[0]
    print(f"📄 Ancho: {page.width}, Alto: {page.height}\n")

    print("🔹 Primeros 20 objetos de texto (x0 → x1):\n")
    for i, char in enumerate(page.chars[:20]):
        print(f"{i+1:02d}: x0={char['x0']:.1f}, x1={char['x1']:.1f}, text='{char['text']}'")
