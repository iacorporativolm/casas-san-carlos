"""Pentágono Bienes Raíces (pentagonobr.com) — WordPress + JetEngine.

Los datos estructurados (precio, moneda, recámaras, baños, m², ID) vienen de
la API REST pública de WordPress (/wp-json/wp/v2/propiedades). La API no
expone colonia ni fotos, así que se abre la ficha HTML de cada propiedad de
San Carlos para leer la colonia visible, la galería y el asesor.
El mapa de la ficha es un embed de Google Maps por texto (sin coordenadas),
por lo que estas propiedades se geocodifican por colonia (aproximada).
"""
from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_baths

SOURCE = "Pentágono Bienes Raíces"
SITE = "https://pentagonobr.com/"
API = "https://pentagonobr.com/wp-json/wp/v2/propiedades"
DELAY = 2.5
LAST_ERRORS: list[str] = []

SAN_CARLOS_CLASSES = {"municipio-o-poblado-san-carlos", "municipio-o-poblado-guaymas"}


def _strip_html(s: str | None) -> str | None:
    if not s:
        return None
    soup = BeautifulSoup(s, "lxml")
    for br in soup.find_all(["br", "p", "li"]):
        br.append("\n")
    return clean_text(htmllib.unescape(soup.get_text()))


def _type_from(classes: list[str], text: str) -> tuple[str | None, str | None]:
    raw = next((c.replace("tipo-de-propiedad-", "") for c in classes if c.startswith("tipo-de-propiedad-")), None)
    if raw is None:
        return None, None
    if raw.startswith("terreno") or raw in ("campestre-ranchos", "terreno-campestre"):
        return "Terreno", raw
    if raw in ("oficina", "local-comercial", "bodega", "negocios"):
        return "Comercial", raw
    if raw == "departamento":
        return "Departamento / Condominio", raw
    if raw == "residencial":
        # La categoría del sitio es "Residencial"; el tipo concreto solo si el anuncio lo dice.
        t = text.lower()
        if re.search(r"\b(departamento|condominio|depto|condo)\b", t):
            return "Departamento / Condominio", raw
        if re.search(r"\b(townhouse|villa)\b", t):
            return "Townhouse / Villa", raw
        if re.search(r"\b(casa|residencia)\b", t):
            return "Casa", raw
        return None, raw
    return raw, raw


def _detail(fetcher, url: str, listing_id: str | None) -> dict:
    html = fetcher.get_html(url)
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    out: dict = {}
    # Galería: widget "compact image theme" de Elementor
    photos = []
    for img in soup.select(".elementor-widget-ucaddon_uc_compact_image_theme img"):
        u = img.get("data-src") or img.get("src")
        if u and "/wp-content/uploads/" in u and not re.search(r"-\d+x\d+\.\w+$", u):
            photos.append(u)
    out["photos"] = list(dict.fromkeys(photos))
    # Ubicación visible tras el ID: "CH-XXX  Country Club , San Carlos  5 Recámaras"
    #                           o "CH-XXX  Marina Real ,Sur ,Guaymas  6 Recámaras"
    if listing_id:
        m = re.search(re.escape(listing_id) + r"\s+([^$]{2,120}?)\s+(?:\d+(?:\s+1/2)?\s+Rec|\d[\d,\.]*m²|Residencial|Venta|Renta|Terreno|Descripci)", text)
        if m and "," in m.group(1):
            parts = [p.strip() for p in m.group(1).split(",")]
            if parts[0] and not parts[0].startswith("ID"):
                out["zone"] = parts[0]
            out["municipio_visible"] = parts[-1] or None
    # Asesor
    m = re.search(r"(?:Venta|Renta)\s+\$[\d,\.]+\s+(?:MXN|USD)\s+(.{3,60}?)\s+Asesor[a]?\s+Inmobiliari[oa]\s+(\+?\d[\d \-]{7,}\d)(?:\s+([\w\.\-+]+@[\w\.\-]+\.\w+))?", text)
    if m:
        out["contact"] = {"agency": SOURCE, "agent": m.group(1).strip(), "phone": m.group(2).strip(),
                          "email": m.group(3)}
    else:
        out["contact"] = {"agency": SOURCE, "agent": None, "phone": "+52 (662) 210 2452", "email": None}
    return out


def scrape(fetcher):
    LAST_ERRORS.clear()
    notes = []
    items = []
    page = 1
    while True:
        r = fetcher.get(API, params={"per_page": 100, "page": page})
        batch = r.json()
        items.extend(batch)
        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages or not batch:
            break
        page += 1
    notes.append(f"{len(items)} propiedades totales en la API; se filtran las de San Carlos/Guaymas")

    out = []
    for it in items:
        classes = it.get("class_list") or []
        if not SAN_CARLOS_CLASSES & set(classes):
            continue
        if "clasificacion-venta" not in classes:
            continue
        url = it["link"]
        title = clean_text(htmllib.unescape(it["title"]["rendered"]))
        desc = _strip_html(it.get("descripcion-general")) or _strip_html(it.get("descripcion-corta"))
        ptype, raw_type = _type_from(classes, f"{title} {desc or ''}")
        lid = it.get("id-de-propiedad") or None
        det = {}
        if ptype != "Comercial":
            try:
                det = _detail(fetcher, url, lid)
            except Exception as e:  # noqa: BLE001
                LAST_ERRORS.append(f"ficha {url}: {e}")
        if not det.get("zone"):
            # Respaldo: término de taxonomía "colonia" asignado por el propio sitio
            col = next((c[len("colonia-"):] for c in classes if c.startswith("colonia-")), None)
            if col:
                det["zone"] = col.replace("-", " ").title()
        scraped = now_iso()
        price = it.get("price") or it.get("precio_propiedad") or None
        rec = make_listing(
            source=SOURCE, source_url=url, listing_id=lid, scraped_at=scraped,
            title=title, price=price, currency=(it.get("moneda") or None),
            property_type=ptype, bedrooms=it.get("recamaras") or None,
            bathrooms=parse_baths(it.get("num-de-banos")),
            construction_m2=it.get("construccion") or None, land_m2=it.get("terreno") or None,
            zone=det.get("zone"), address=None, description=desc, photos=det.get("photos") or [],
            contact=det.get("contact"), published_at=(it.get("date_gmt") + "Z") if it.get("date_gmt") else None,
            extra={"categoria_sitio": raw_type,
                   "municipio_sitio": next((c.replace("municipio-o-poblado-", "") for c in classes if c.startswith("municipio-o-poblado-")), None)},
        )
        rec["_in_san_carlos_search"] = "municipio-o-poblado-san-carlos" in classes
        out.append(rec)
    return out, notes
