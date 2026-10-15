"""Sonora MLS Regional — San Carlos MLS (listings.sancarlosmls.mx), AMPI Guaymas-San Carlos.

Portal público del MLS local (plataforma Xposure/realtyserver) que agrupa a
varias inmobiliarias de San Carlos. robots.txt sin restricciones.
- Lista paginada /pageNumber_N con tarjetas (id, nombre, precio, inmobiliaria).
- Ficha /listing/<id>: tabla Address/Area/Sub Area/Price/Property Type/
  Bedrooms/Bathrooms/Floor Space/Lot Size/MLS#, descripción, agente,
  galería y coordenadas (ws_lat/ws_lon del mapa del anuncio).
Moneda: los precios en pesos aparecen como "MXN 3,000,000". Los que aparecen
como "$920,000" usan la moneda base que el portal declara en su listado
(app.setCurrencySymbol("USD")); la base se guarda en extra.moneda_fuente.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_area, parse_baths, parse_number

SOURCE = "San Carlos MLS (Sonora MLS Regional)"
SITE = "https://listings.sancarlosmls.mx/"
DELAY = 2.5
LAST_ERRORS: list[str] = []
# Etiquetas de la ficha: el MLS responde en español o inglés según Accept-Language
LABELS = {"Dirección": "Address", "Área": "Area", "Barrio": "Sub Area", "Precio": "Price",
          "Tipo de Propiedad": "Property Type", "Dormitorios": "Bedrooms", "Baños": "Bathrooms",
          "Area de Construida": "Floor Space", "Área de Construida": "Floor Space",
          "Area del Lote": "Lot Size", "Área del Lote": "Lot Size"}
TYPE_MAP = {"single family": "Casa", "house": "Casa", "casa": "Casa", "condo": "Departamento / Condominio",
            "departamento": "Departamento / Condominio", "terreno": "Terreno", "lote": "Terreno",
            "casa adosada": "Townhouse / Villa", "dúplex": "Townhouse / Villa", "multifamiliar": "Casa",
            "condominio": "Departamento / Condominio", "apartment": "Departamento / Condominio",
            "townhouse": "Townhouse / Villa", "duplex": "Townhouse / Villa", "multi-family": "Casa",
            "land": "Terreno", "lot": "Terreno", "vacant land": "Terreno", "commercial": "Comercial",
            "farm": "Rancho / Quinta", "recreational": "Rancho / Quinta"}


def _cards(fetcher):
    seen, page = {}, 1
    while page <= 40:
        url = SITE if page == 1 else f"{SITE}pageNumber_{page}"
        html = fetcher.get_html(url)
        soup = BeautifulSoup(html, "lxml")
        # Moneda base que el propio portal configura para los precios con "$"
        sym = re.search(r'app\.setCurrencySymbol\("([A-Z]{3})\s*"\)', html)
        new = 0
        for a in soup.select('a[href*="/listing/"][data-id]'):
            lid = a["data-id"]
            if lid in seen:
                continue
            pr = a.select_one(".price")
            seen[lid] = {"url": a["href"], "agency": clean_text(a.select_one(".agt").get_text()) if a.select_one(".agt") else None,
                         "price_text": clean_text(pr.get_text(" ")) if pr else None,
                         "site_currency": sym.group(1) if sym else None}
            new += 1
        if not new:
            break
        page += 1
    return seen, page - 1


def _detail(fetcher, url):
    html = fetcher.get_html(url)
    soup = BeautifulSoup(html, "lxml")
    fields = {}
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2 and tds[0].find("strong"):
            label = clean_text(tds[0].get_text())
            fields[LABELS.get(label, label)] = clean_text(tds[1].get_text(" "))
    desc_el = soup.select_one(".detail-tab-content p")
    desc = None
    if desc_el:
        for br in desc_el.find_all("br"):
            br.replace_with("\n")
        desc = clean_text(desc_el.get_text())
    lat = re.search(r"var ws_lat = '(-?\d+\.\d+)'", html)
    lng = re.search(r"var ws_lon = '(-?\d+\.\d+)'", html)
    photos = list(dict.fromkeys(re.findall(r"https://van-media\.xposureapp\.com/media/[^\"' )<>]+\.(?:jpe?g|png|webp)", html, re.I)))
    agent = soup.select_one(".info.name")
    cell = soup.select_one(".phone.cell-phone") or soup.select_one(".phone")
    office = soup.select_one(".company-name")
    title = soup.find("title")
    return {
        "fields": fields, "desc": desc, "photos": photos,
        "lat": lat.group(1) if lat else None, "lng": lng.group(1) if lng else None,
        "agent": " ".join(agent.get_text(" ").split()) if agent else None,
        "phone": re.sub(r"^[CP]\.\s*", "", cell.get_text(" ", strip=True)) if cell else None,
        "office": clean_text(office.get_text(" ")) if office else None,
        "title": clean_text(title.get_text()) if title else None,
    }


def scrape(fetcher):
    LAST_ERRORS.clear()
    cards, pages = _cards(fetcher)
    notes = [f"{len(cards)} anuncios activos en {pages} páginas del MLS (todas las áreas y tipos)",
             'Precios con "$" = moneda base declarada por el portal (setCurrencySymbol); "MXN ..." = pesos']
    out = []
    for lid, card in cards.items():
        url = card["url"]
        try:
            d = _detail(fetcher, url)
        except Exception as e:  # noqa: BLE001
            LAST_ERRORS.append(f"ficha {url}: {e}")
            continue
        f = d["fields"]
        area = f.get("Area")
        ptype_raw = (f.get("Property Type") or "").strip()
        ptype = TYPE_MAP.get(ptype_raw.lower(), ptype_raw or None)
        floor_m2, floor_orig = parse_area(*(f.get("Floor Space") or " ").split(" ", 1)) if f.get("Floor Space") else (None, None)
        lot_m2, lot_orig = parse_area(*(f.get("Lot Size") or " ").split(" ", 1)) if f.get("Lot Size") else (None, None)
        address = ", ".join(x for x in (f.get("Address"), f.get("Sub Area"), area) if x)
        name = (d["title"] or "").split(" - ")[0] or None
        # Moneda: "MXN 3,000,000" la declara el anuncio; "$920,000" usa la moneda base
        # que el portal configura en su listado (app.setCurrencySymbol("USD")).
        ptxt = " ".join(x for x in (f.get("Price"), card.get("price_text")) if x)
        if "MXN" in ptxt.upper():
            currency, basis = "MXN", "el anuncio dice MXN"
        elif "USD" in ptxt.upper():
            currency, basis = "USD", "el anuncio dice USD"
        elif ptxt.strip().startswith("$") and card.get("site_currency") in ("USD", "MXN"):
            currency = card["site_currency"]
            basis = f'precio con "$": moneda base del portal (app.setCurrencySymbol("{currency}") en su listado)'
        else:
            currency, basis = None, "el portal no indica la moneda"
        rec = make_listing(
            source=SOURCE, source_url=url, listing_id=f.get("MLS® #") or lid, scraped_at=now_iso(),
            title=name, price=parse_number(f.get("Price")), currency=currency,
            property_type=ptype, bedrooms=f.get("Bedrooms"), bathrooms=parse_baths(f.get("Bathrooms")),
            construction_m2=floor_m2, land_m2=lot_m2, zone=f.get("Sub Area") or area,
            address=address or None, description=d["desc"], photos=d["photos"],
            contact={"agency": d["office"] or card.get("agency"), "agent": d["agent"], "phone": d["phone"], "email": None},
            lat=d["lat"], lng=d["lng"],
            extra={"mls_san_carlos": f.get("MLS® #"), "area_mls": area, "tipo_mls": ptype_raw,
                   "precio_publicado": ptxt or None, "moneda_fuente": basis,
                   "area_publicada": floor_orig, "terreno_publicado": lot_orig},
        )
        rec["_in_san_carlos_search"] = (area or "").strip().lower() == "san carlos"
        out.append(rec)
    return out, notes
