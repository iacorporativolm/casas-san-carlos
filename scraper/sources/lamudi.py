"""Lamudi México (lamudi.com.mx).

robots.txt (tal como lo sirve a este cliente) permite páginas de búsqueda y de
detalle a todos los agentes; solo prohíbe APIs internas, formularios y los
parámetros ?geos=, version=, action=, search-filter (no se usan).
Nota: el sitio tiene un filtro anti-bot que a veces responde 401 "Access Denied";
si eso pasa, la fuente se reporta como bloqueada y no se insiste.
- Búsqueda: /sonora/guaymas/san-carlos-nuevo-guaymas/{casa|departamento}/for-sale/?page=N
- Ficha: JSON-LD (SingleFamilyResidence/Apartment con recámaras, baños, m²,
  dirección y geo; RealEstateListing con fecha y fotos), precio visible,
  "Superficie de terreno", categoría y "Publicado por <anunciante>".
Lamudi es un portal: muchas fichas son de inmobiliarias que también tienen
sitio propio (p. ej. NEXT, Century 21); la deduplicación las une.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..common import BlockedError, clean_text, extract_json_ld, make_listing, now_iso, parse_number

SOURCE = "Lamudi"
SITE = "https://www.lamudi.com.mx/"
BASE = "https://www.lamudi.com.mx/sonora/guaymas/san-carlos-nuevo-guaymas/{t}/for-sale/"
DELAY = 3.0
LAST_ERRORS: list[str] = []
PARTIAL_BLOCK = False  # True si el sitio empezó a pedir verificación humana a mitad de la corrida
TYPES = {"casa": "Casa", "departamento": "Departamento / Condominio", "terreno": "Terreno"}
RES_TYPES = {"SingleFamilyResidence", "House", "Apartment", "Residence", "Accommodation", "Place"}


def _list(fetcher, t):
    urls, page, last = [], 1, 1
    while page <= last and page <= 20:
        soup = BeautifulSoup(fetcher.get_html(BASE.format(t=t), params={"page": page} if page > 1 else None), "lxml")
        found = [urljoin(SITE, a["href"].split("?")[0]) for a in soup.select('.snippet a[href*="/detalle/"]')]
        urls.extend(found)
        pages = [int(p) for a in soup.find_all("a", href=True) for p in re.findall(r"[?&]page=(\d+)", a["href"])]
        last = max(pages + [page])
        if not found:
            break
        page += 1
    return list(dict.fromkeys(urls))


def _detail(fetcher, url):
    soup = BeautifulSoup(fetcher.get_html(url), "lxml")
    res, listing = {}, {}
    for ld in extract_json_ld(soup):
        t = ld.get("@type")
        ld_id = (ld.get("@id") or "").rstrip("/")
        if ld_id == url.rstrip("/") and t != "BreadcrumbList":
            res = ld  # objeto principal del inmueble (casa/depto: SingleFamilyResidence...; terreno: RealEstateListing)
        elif t in RES_TYPES and not res:
            res = ld
        if t == "RealEstateListing" and ld_id.endswith("#page"):
            listing = ld
    price_el = soup.select_one(".prices-and-fees__price.sale") or soup.select_one(".prices-and-fees__price")
    feats = {}
    for lab in soup.select(".features-component__label"):
        val = lab.find_next_sibling()
        if val:
            feats[clean_text(lab.get_text())] = clean_text(val.get_text(" "))
    half = None
    for it in soup.select(".place-details .details-item-value"):
        m = re.match(r"(\d+)\s+Medio", it.get_text(strip=True))
        if m:
            half = int(m.group(1))
    pub = soup.select_one(".left-details .date") or soup.select_one(".date")
    publisher = None
    if pub:
        m = re.search(r"Publicado por (.+)$", pub.get_text(" ", strip=True))
        publisher = m.group(1).strip() if m else None
    h1 = soup.find("h1")
    return {"res": res, "listing": listing, "price": clean_text(price_el.get_text(" ")) if price_el else None,
            "feats": feats, "half": half, "publisher": publisher, "h1": clean_text(h1.get_text()) if h1 else None}


def scrape(fetcher):
    global PARTIAL_BLOCK
    LAST_ERRORS.clear()
    PARTIAL_BLOCK = False
    notes, out = [], []
    for t, ptype in TYPES.items():
        if PARTIAL_BLOCK:
            break
        try:
            urls = _list(fetcher, t)
        except BlockedError as e:
            if not out:
                raise
            PARTIAL_BLOCK = True
            LAST_ERRORS.append(f"búsqueda {t}: {e}")
            break
        notes.append(f"{t}: {len(urls)} anuncios en venta (San Carlos Nuevo Guaymas)")
        for url in urls:
            try:
                d = _detail(fetcher, url)
            except BlockedError as e:
                # El sitio pide verificación humana: se detiene aquí, sin evadirla.
                PARTIAL_BLOCK = True
                LAST_ERRORS.append(f"Verificación humana a partir de {url} tras {len(out)} fichas leídas: {e}")
                break
            except Exception as e:  # noqa: BLE001
                LAST_ERRORS.append(f"ficha {url}: {type(e).__name__}: {e}")
                continue
            if not d["res"] and not d["price"]:
                LAST_ERRORS.append(f"ficha sin datos (no se guarda): {url}")
                continue
            res, lst, f = d["res"], d["listing"], d["feats"]
            if (f.get("Operación") or "Venta").lower() != "venta":
                continue
            pr = d["price"] or ""
            cur = "MXN" if "MXN" in pr else "USD" if ("USD" in pr or "US$" in pr) else None
            adr = (res.get("address") or {}).get("streetAddress")
            zone = None
            if adr:
                first = adr.split(",")[0].strip()
                if first and not re.search(r"(?i)san carlos|guaymas|sonora|méxico", first):
                    zone = first
            geo = res.get("geo") or {}
            images = lst.get("image") or res.get("image") or []
            images = images if isinstance(images, list) else [images]
            land = parse_number(f.get("Superficie de terreno"))
            fl = res.get("floorSize") or {}
            fl_m2 = parse_number(fl.get("value")) if (fl.get("unitCode") in (None, "MTK")) else None
            if ptype == "Terreno":
                constr, land = None, land or fl_m2  # en terrenos, la superficie publicada es la del lote
            else:
                constr = fl_m2
            rec = make_listing(
                source=SOURCE, source_url=url, listing_id=url.rstrip("/").split("/")[-1], scraped_at=now_iso(),
                title=d["h1"] or res.get("name"), price=parse_number(pr) if pr else None, currency=cur,
                property_type=ptype, bedrooms=res.get("numberOfBedrooms"),
                bathrooms=res.get("numberOfBathroomsTotal"), construction_m2=constr, land_m2=land,
                zone=zone, address=adr, description=clean_text(res.get("description")), photos=images,
                contact={"agency": d["publisher"], "agent": None, "phone": None, "email": None},
                lat=geo.get("latitude"), lng=geo.get("longitude"), published_at=lst.get("datePosted"),
                extra={"medios_banos": d["half"], "categoria_lamudi": f.get("Categoría")},
            )
            rec["_in_san_carlos_search"] = True  # búsqueda por la localidad San Carlos Nuevo Guaymas del portal
            out.append(rec)
    return out, notes
