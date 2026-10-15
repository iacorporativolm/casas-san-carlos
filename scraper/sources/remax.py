"""RE/MAX México (remax.com.mx).

El buscador del sitio carga resultados por XHR desde endpoints públicos que usa
el propio frontend:
  POST /map/FetchMapData            -> lista (precio, recámaras, m², colonia, lat/lng)
  POST /ajax/FetchPropiedadFlyerData/<id> -> ficha completa (descripción, zona, agente)
robots.txt solo prohíbe /admin/, /agente/ y /super/.
RE/MAX no tiene una "ciudad" San Carlos: está dentro de Guaymas (ciudad_id 12065).
Se conservan las propiedades cuya zona/colonia/CP del anuncio indica San Carlos.
Las coordenadas vienen del anuncio (las captura el agente).
"""
from __future__ import annotations

import html as htmllib

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_baths

SOURCE = "RE/MAX México"
SITE = "https://www.remax.com.mx/"
DELAY = 3.0
LAST_ERRORS: list[str] = []

GUAYMAS_CITY_ID = "12065"
RESIDENTIAL_TIPOS = {"1": "Casa", "2": "Casa en Condominio", "3": "Departamento"}
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _html_to_text(s):
    if not s:
        return None
    soup = BeautifulSoup(s, "lxml")
    for t in soup.find_all(["br", "p", "li"]):
        t.append("\n")
    return clean_text(htmllib.unescape(soup.get_text()))


def scrape(fetcher):
    LAST_ERRORS.clear()
    notes = []
    found = {}
    for tipo, tname in RESIDENTIAL_TIPOS.items():
        r = fetcher.post(
            "https://www.remax.com.mx/map/FetchMapData",
            files={k: (None, v) for k, v in {"moneda": "MXN", "locationKeyword": "Guaymas, Sonora",
                                             "ciudad_id": GUAYMAS_CITY_ID, "tipo": tipo, "operacion": "1"}.items()},
            headers={**XHR, "Referer": "https://www.remax.com.mx/propiedades/Guaymas_Sonora/residencial-casa/venta"},
        )
        rows = (r.json().get("data") or {}).get("prop_data") or []
        notes.append(f"{tname}: {len(rows)} en Guaymas (venta)")
        for row in rows:
            found[row["propiedad_id"]] = row

    out = []
    for pid, row in found.items():
        url = f"https://www.remax.com.mx/propiedad/{pid}"
        try:
            d = fetcher.post(f"https://www.remax.com.mx/ajax/FetchPropiedadFlyerData/{pid}",
                             headers={**XHR, "Referer": url}).json()["data"]
        except Exception as e:  # noqa: BLE001
            LAST_ERRORS.append(f"ficha {pid}: {e}")
            continue
        p = d["propiedad"]
        ag = d.get("agente") or {}
        of = d.get("oficina") or {}
        ccy = p.get("moneda") or row.get("moneda")
        price = p.get("usd_corriente") if ccy == "USD" else p.get("mxn_corriente")
        calle = " ".join(x for x in (p.get("calle"), p.get("numero_exterior")) if x and x != "0")
        address = ", ".join(x for x in (calle, p.get("colonia_nombre"), f"CP {p['postal']}" if p.get("postal") else None) if x)
        photos = [f"https://cdn.remax.com.mx/{im['path']}" for im in (p.get("imagenes") or []) if im.get("path")]
        agent_name = " ".join(x for x in (ag.get("nombre"), ag.get("apellido")) if x) or None
        rec = make_listing(
            source=SOURCE, source_url=url, listing_id=p.get("clave") or pid, scraped_at=now_iso(),
            title=p.get("titulo"), price=price, currency=ccy,
            property_type=p.get("tipo_nombre"), bedrooms=p.get("cuartos"), bathrooms=parse_baths(p.get("banos")),
            construction_m2=p.get("m2_construccion"), land_m2=p.get("m2_terreno"),
            zone=p.get("colonia_nombre"), address=address or None,
            description=_html_to_text(p.get("descripcion")), photos=photos,
            contact={"agency": of.get("oficina_nombre"), "agent": agent_name,
                     "phone": ag.get("celular") or ag.get("telefono") or of.get("oficina_telefono"),
                     "email": ag.get("email") or of.get("oficina_email")},
            lat=p.get("latitud"), lng=p.get("longitud"),
            published_at=p.get("fecha_publica"),
            extra={"zona_remax": p.get("zona"), "postal": p.get("postal"), "ciudad": p.get("ciudad_nombre")},
        )
        zona = (p.get("zona") or "").lower()
        rec["_in_san_carlos_search"] = "san carlos" in zona
        out.append(rec)
    return out, notes
