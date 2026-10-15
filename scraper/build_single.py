"""Genera casas-san-carlos.html: el sitio completo en UN solo archivo (CSS, JS,
Leaflet y los datos incrustados) que se abre con doble clic, sin servidor.
Uso: python3 -m scraper.build_single   (refresh lo llama al terminar)
El mapa (teselas de OpenStreetMap) y las fotos se cargan de internet.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "casas-san-carlos.html"


def _js_safe(text: str) -> str:
    # Evita que un "</script>" dentro de los datos o del código cierre la etiqueta
    return text.replace("</", "<\\/")


def build() -> Path:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    read = lambda p: (ROOT / p).read_text(encoding="utf-8")

    def inline_css(m):
        return f"<style>\n{read(m.group(1))}\n</style>"

    def inline_js(m):
        return f"<script>\n{_js_safe(read(m.group(1)))}\n</script>"

    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', inline_css, html)
    data = json.loads(read("data/listings.json"))
    report = json.loads(read("data/sources_report.json")) if (ROOT / "data/sources_report.json").exists() else None
    payload = (f"window.__SC_DATA__ = {json.dumps(data, ensure_ascii=False)};\n"
               f"window.__SC_REPORT__ = {json.dumps(report, ensure_ascii=False)};")
    # Datos antes de app.js
    html = html.replace('<script src="assets/app.js"></script>',
                        f"<script>\n{_js_safe(payload)}\n</script>\n<script src=\"assets/app.js\"></script>")
    html = re.sub(r'<script src="([^"]+)"></script>', inline_js, html)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"Generado {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
