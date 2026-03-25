import pdfplumber

PDF_PATH = "albaran_pollo_260109_123335.pdf"

with pdfplumber.open(PDF_PATH) as pdf:
    page = pdf.pages[0]
    print("\n📄 Información del PDF")
    print("Ancho:", page.width)
    print("Alto:", page.height)

    text = page.extract_text() or ""
    print("\n🧾 Inicio del texto:\n")
    print(text[:500])
