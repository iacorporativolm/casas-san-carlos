"""RE/MAX First Choice Realty San Carlos (remax-firstchoice-sancarlosmexico.com).

Broker local. robots.txt pide Crawl-Delay: 20 (se respeta).
1) La página de búsqueda /en/properties-search/ lista todas las tarjetas con
   estatus (For Sale / Sold / Under Contract) y tipo (House, Condominium, Land…).
2) Para cada tarjeta "For Sale" residencial se abre la ficha: JSON-LD
   RealEstateListing (precio, moneda, dirección, descripción), bloque rw-*
   (recámaras, baños, m²), número MLS de San Carlos, coordenadas data-lat/lng
   del mapa y galería.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_area, parse_baths, parse_number

SOURCE = "RE/MAX First Choice San Carlos"
SITE = "https://remax-firstchoice-sancarlosmexico.com/"
SEARCH = "https://remax-firstchoice-sancarlosmexico.com/en/properties-search/"
DELAY = 20.0  # Crawl-Delay del robots.txt
LAST_ERRORS: list[str] = []
RESIDENTIAL = {"House": "Casa", "Condominium": "Departamento / Condominio", "Townhouse": "Townhouse / Villa",
               "Villa": "Townhouse / Villa", "Apartment": "Departamento / Condominio"}


def _detail(fetcher, url):
    soup = BeautifulSoup(fetcher.get_html(url), "lxml")
    ld = {}
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(sc.get_text())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "RealEstateListing":
            ld = d
    out = {"ld": ld}
    el = soup.find(attrs={"data-lat": True, "data-lng": True})
    if el:
        out["lat"], out["lng"] = el["data-lat"], el["data-lng"]
    mls = soup.select_one(".rw-mls")
    if mls:
        m = re.search(r"\d+", mls.get_text())
        out["mls"] = m.group(0) if m else None
    num = lambda sel: (soup.select_one(f"{sel} .rw-num").get_text(" ", strip=True) if soup.select_one(f"{sel} .rw-num") else None)
    out["bedrooms"] = num(".rw-bedrooms")
    out["bathrooms"] = num(".rw-bathrooms")
    for item in soup.select(".rw-meters-sq"):
        n = item.select_one(".rw-num")
        sub = item.select_one(".rw-item-sub-text")
        if not n or not sub:
            continue
        txt = n.get_text(" ", strip=True)
        if "m" not in txt.lower() or "sqft" in txt.lower():
            continue  # se usa la cifra en m² que publica el sitio, no la convertida a sqft
        val, orig = parse_area(txt.split()[0], "m2")
        key = "construction" if sub.get_text(strip=True).lower().startswith("casa") else "land"
        out.setdefault(key, val)
    photos = [a["href"] for a in soup.select(".soliloquy-slider a[href]") if re.search(r"\.(webp|jpe?g|png)$", a["href"], re.I)]
    out["photos"] = list(dict.fromkeys(photos))
    pr = soup.select_one(".rw-price")
    out["price_text"] = clean_text(pr.get_text(" ")) if pr else None
    a2 = soup.select_one(".rw-address2")
    out["address2"] = clean_text(a2.get_text(" ", strip=True)) if a2 else None
    phone = soup.select_one(".rw-header-text")
    out["office_phone"] = re.search(r"\+?\d[\d \-]{8,}\d", phone.get_text()).group(0) if phone and re.search(r"\+?\d[\d \-]{8,}\d", phone.get_text()) else None
    return out


def scrape(fetcher):
    LAST_ERRORS.clear()
    soup = BeautifulSoup(fetcher.get_html(SEARCH), "lxml")
    cards = soup.select("article.item")
    notes = [f"{len(cards)} tarjetas en la búsqueda"]
    out = []
    for art in cards:
        a = art.find("a", href=True)
        rib = art.select_one(".ribbon")
        status = [c for c in (rib.get("class") if rib else []) if c not in ("ribbon", "ribbon-top-right")]
        ptype_raw = art.select_one(".rw-tags .type")
        ptype_raw = ptype_raw.get_text(strip=True) if ptype_raw else None
        if "for-sale" not in status or ptype_raw not in RESIDENTIAL:
            continue
        url = a["href"]
        sub = art.select_one(".rw-tags .subdivision")
        try:
            d = _detail(fetcher, url)
        except Exception as e:  # noqa: BLE001
            LAST_ERRORS.append(f"ficha {url}: {e}")
            continue
        ld = d["ld"]
        offer = ld.get("offers") or {}
        adr = ld.get("address") or {}
        address = ", ".join(x for x in (adr.get("streetAddress"), adr.get("addressLocality"),
                                         f"CP {adr['postalCode']}" if adr.get("postalCode") else None) if x)
        # Precio visible en la ficha ("$420,000 USD"); el JSON-LD a veces no lo trae
        ptxt = d.get("price_text") or ""
        price = parse_number(ptxt) if re.search(r"\d", ptxt) else parse_number(offer.get("price"))
        currency = ("USD" if "USD" in ptxt.upper() else "MXN" if ("MXN" in ptxt.upper() or "PESOS" in ptxt.upper()) else
                    offer.get("priceCurrency") if offer.get("priceCurrency") in ("USD", "MXN") else None)
        rec = make_listing(
            source=SOURCE, source_url=url, listing_id=d.get("mls"), scraped_at=now_iso(),
            title=ld.get("name") or (a.get("title")), price=price, currency=currency,
            property_type=RESIDENTIAL[ptype_raw], bedrooms=d.get("bedrooms"),
            bathrooms=parse_baths(d.get("bathrooms")), construction_m2=d.get("construction"),
            land_m2=d.get("land"), zone=clean_text(sub.get_text()) if sub else None,
            address=address or None, description=clean_text(ld.get("description")),
            photos=d.get("photos") or ([ld["image"]] if ld.get("image") else []),
            contact={"agency": "RE/MAX First Choice Realty San Carlos", "agent": None,
                     "phone": d.get("office_phone"), "email": None},
            lat=d.get("lat"), lng=d.get("lng"),
            extra={"mls_san_carlos": d.get("mls"), "precio_publicado": ptxt or None},
        )
        rec["_in_san_carlos_search"] = "SAN CARLOS" in (adr.get("addressLocality") or "").upper()
        out.append(rec)
    return out, notes
