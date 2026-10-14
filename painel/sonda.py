"""Sonda temporaria: baixa textos de Mori Ogai do Aozora Bunko (dominio publico)."""
import io, re, zipfile, urllib.request, json, os
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
        return r.read()
os.makedirs("painel/ogai", exist_ok=True)
idx = get("https://www.aozora.gr.jp/index_pages/person129.html").decode("utf-8", "replace")
alvos = ["高瀬舟", "最後の一句", "寒山拾得", "杯", "高瀬舟縁起"]
achados = {}
for m in re.finditer(r'<a href="\.\./cards/000129/card(\d+)\.html">([^<]+)</a>', idx):
    if m.group(2) in alvos and m.group(2) not in achados:
        achados[m.group(2)] = m.group(1)
print(achados)
for titulo, card in achados.items():
    pg = get(f"https://www.aozora.gr.jp/cards/000129/card{card}.html").decode("utf-8", "replace")
    z = re.search(r'href="\./files/([^"]+\.zip)"', pg)
    if not z:
        continue
    zf = zipfile.ZipFile(io.BytesIO(get(f"https://www.aozora.gr.jp/cards/000129/files/{z.group(1)}")))
    nome = [n for n in zf.namelist() if n.endswith(".txt")][0]
    txt = zf.read(nome).decode("shift_jis", "replace")
    open(f"painel/ogai/{card}.txt", "w", encoding="utf-8").write(f"# {titulo}\n" + txt)
    print(titulo, card, len(txt))
