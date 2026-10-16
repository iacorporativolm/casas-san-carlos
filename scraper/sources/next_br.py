"""NEXT Bienes Raíces (nextbr.mx) — inmobiliaria con oficina en San Carlos.

robots.txt: "Disallow:" vacío (todo permitido).
- Búsqueda: /propiedades-clasica?f=1&city_id=23 (San Carlos, Sonora) &operation=1 (venta)
  &type_id=Casas|Departamentos, paginada.
- Ficha: título, dirección, precio en su moneda original (el sitio muestra además una
  conversión a USD que NO se usa), descripción, ID (SACV-xxxx), características
  (Cuartos, Baños, Medios baños, Construcción, Terreno), asesor, galería y
  coordenadas del anuncio (inputs ocultos #lat/#lng que alimentan su mapa).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_number

SOURCE = "NEXT Bienes Raíces"
SITE = "https://www.nextbr.mx/"
LIST = "https://www.nextbr.mx/propiedades-clasica"
DELAY = 2.5
LAST_ERRORS: list[str] = []
TYPES = {"Casas": "Casa", "Departamentos": "Departamento / Condominio",
         "Terrenos Residenciales": "Terreno", "Terrenos Campestres": "Terreno",
         "Terrenos Comerciales": "Terreno", "Terrenos": "Terreno"}


def _list(fetcher, type_id):
    urls, page, last = [], 1, 1
    while page <= last and page <= 60:
        soup = BeautifulSoup(fetcher.get_html(LIST, params={"f": 1, "city_id": 23, "operation": 1,
                                                            "type_id": type_id, "page": page}), "lxml")
        for card in soup.select("div.col-md-4.mb-5"):
            a = card.find("a", href=re.compile(r"/propiedades/"))
            if a:
                urls.append(urljoin(SITE, a["href"]))
        pages = [int(p) for a in soup.find_all("a", href=True) for p in re.findall(r"[?&]page=(\d+)", a["href"])]
        last = max(pages + [page])
        page += 1
    return list(dict.fromkeys(urls))


def _detail(fetcher, url):
    soup = BeautifulSoup(fetcher.get_html(url), "lxml")
    art = soup.select_one("article.col-lg-8") or soup
    h2 = art.find("h2")
    addr = art.select_one("p.mb-0 i.fa-map-marker-alt")
    price_p = art.select_one("p.fs-22")
    feats = {}
    for mb in art.select(".media-body"):
        k, v = mb.find("h5"), mb.find("p")
        if k and v:
            feats[clean_text(k.get_text()).lower()] = clean_text(v.get_text(" "))
    # Descripción: párrafos entre "Descripción" y "ID:"
    desc_parts, lid = [], None
    h4 = art.find("h4", string=re.compile("Descripci"))
    if h4:
        for el in h4.find_next_siblings():
            txt = el.get_text("\n", strip=True)
            m = re.match(r"ID:\s*(\S+)", txt)
            if m:
                lid = m.group(1)
                break
            if txt:
                desc_parts.append(txt)
    lat = soup.find(id="lat")
    lng = soup.find(id="lng")
    photos = [urljoin(SITE, i.get("src")) for i in soup.select('img[src*="/uploads/listings/"]')]
    text = soup.get_text(" | ", strip=True)
    agent = re.search(r"Ver en mapa \| (.{3,80}?) \| Contactar vía:(?: \| ([\w\.\-+]+@[\w\.\-]+))?(?: \| Contacto: \| (\+?\d[\d \-]{7,}\d))?", text)
    # El <title> trae "Casa en Venta en San Carlos , Fracc. Punta del Mar, , 4 recámaras, ..."
    colonia = None
    t = soup.find("title")
    m = re.search(r"en Venta en ([^,]+?)\s*,\s*([^,]*?)\s*,", t.get_text() if t else "")
    if m and m.group(2).strip():
        colonia = clean_text(m.group(2))
    return {
        "colonia": colonia,
        "title": clean_text(h2.get_text()) if h2 else None,
        "address": clean_text(addr.parent.get_text(" ")) if addr else None,
        "price_raw": clean_text(price_p.get_text()) if price_p else None,
        "feats": feats, "desc": "\n\n".join(desc_parts) or None, "id": lid,
        "lat": lat.get("value") if lat else None, "lng": lng.get("value") if lng else None,
        "photos": list(dict.fromkeys(photos)),
        "agent": agent.group(1).title() if agent else None,
        "email": agent.group(2) if agent else None,
        "phone": agent.group(3) if agent else None,
        "zone_card": None,
    }


def scrape(fetcher):
    LAST_ERRORS.clear()
    notes = []
    out = []
    seen: set[str] = set()
    for type_id, ptype in TYPES.items():
        urls = [u for u in _list(fetcher, type_id) if u not in seen]
        seen.update(urls)
        notes.append(f"{type_id}: {len(urls)} anuncios en venta (San Carlos)")
        for url in urls:
            try:
                d = _detail(fetcher, url)
            except Exception as e:  # noqa: BLE001
                LAST_ERRORS.append(f"ficha {url}: {e}")
                continue
            pr = d["price_raw"] or ""
            cur = "MXN" if "/MXN" in pr else "USD" if "/USD" in pr else None
            f = d["feats"]
            # Zona: segundo elemento de la dirección ("P.º Isla del Tiburón, Punta del Mar, 85506 San Carlos…")
            zone = d.get("colonia")
            if not zone and d["address"]:
                parts = [p.strip() for p in d["address"].split(",")]
                cand = [p for p in parts if p and not re.search(r"\d{5}|son\.?$|méxico|mexico|sonora", p, re.I)]
                zone = cand[1] if len(cand) >= 2 else (cand[0] if cand else None)
            rec = make_listing(
                source=SOURCE, source_url=url, listing_id=d["id"], scraped_at=now_iso(),
                title=d["title"], price=parse_number(pr.split("/")[0]) if pr else None, currency=cur,
                property_type=ptype, bedrooms=f.get("cuartos"), bathrooms=parse_number(f.get("baños")),
                construction_m2=parse_number(f.get("construcción")), land_m2=parse_number(f.get("terreno")),
                zone=zone, address=d["address"], description=d["desc"], photos=d["photos"],
                contact={"agency": SOURCE, "agent": d["agent"], "phone": d["phone"], "email": d["email"]},
                lat=d["lat"], lng=d["lng"],
                extra={"medios_banos": parse_number(f.get("medios baños")), "pisos": f.get("pisos"),
                       "categoria_sitio": type_id},
            )
            rec["_in_san_carlos_search"] = True  # búsqueda filtrada por ciudad San Carlos en el propio sitio
            out.append(rec)
    return out, notes
