"""Coldwell Banker Encantado Realty — San Carlos (coldwellbankersancarlos.com).

WordPress + tema RealHomes. La API REST pública (/wp-json/wp/v2/properties)
expone property_meta: precio con prefijo/sufijo de moneda, recámaras, baños,
superficies con su unidad, dirección, coordenadas del mapa y galería.
Solo se toman las de estatus "For Sale".
Notas de calidad de datos:
- Superficies sin unidad: el sitio las muestra como "sq ft", así que se leen así.
- La dirección "KM 11.5 S/N, SAN CARLOS" es la de la oficina (valor por defecto):
  no se usa como dirección de la propiedad.
"""
from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup

from ..common import clean_text, make_listing, now_iso, parse_area, parse_baths

SOURCE = "Coldwell Banker San Carlos"
SITE = "https://coldwellbankersancarlos.com/"
BASE = "https://coldwellbankersancarlos.com/wp-json/wp/v2"
DELAY = 2.5
LAST_ERRORS: list[str] = []
NOT_SC_CITIES = {"GUAYMAS", "MIRAMAR", "LAS PERLAS"}
OFFICE_ADDRESS = re.compile(r"^\s*KM\s*11\.5\s*S/N", re.I)


def _paged(fetcher, endpoint):
    out, page = [], 1
    while True:
        r = fetcher.get(f"{BASE}/{endpoint}", params={"per_page": 100, "page": page})
        out.extend(r.json())
        if page >= int(r.headers.get("X-WP-TotalPages", "1")):
            return out
        page += 1


def _text(s):
    if not s:
        return None
    soup = BeautifulSoup(s, "lxml")
    for t in soup.find_all(["br", "p", "li"]):
        t.append("\n")
    return clean_text(htmllib.unescape(soup.get_text()))


def _currency(prefix, postfix):
    t = f"{prefix or ''} {postfix or ''}".upper()
    if "USD" in t or "USA" in t or "US$" in t or "DLLS" in t:
        return "USD"
    if "MXN" in t or "PESO" in t or "MN" in t.split():
        return "MXN"
    return None


def _type(type_names):
    names = set(type_names)
    if "Condominium" in names:
        return "Departamento / Condominio"
    if "House" in names:
        return "Casa"
    if "Lot" in names:
        return "Terreno"
    if "Commercial" in names:
        return "Comercial"
    return None


def scrape(fetcher):
    LAST_ERRORS.clear()
    statuses = {t["id"]: t["name"] for t in _paged(fetcher, "property-statuses")}
    cities = {t["id"]: t["name"] for t in _paged(fetcher, "property-cities")}
    types = {t["id"]: t["name"] for t in _paged(fetcher, "property-types")}
    props = _paged(fetcher, "properties")
    for_sale_id = next(k for k, v in statuses.items() if v.lower() == "for sale")
    notes = [f"{len(props)} propiedades en la API; {sum(for_sale_id in p['property-statuses'] for p in props)} con estatus For Sale"]
    agents_cache: dict[str, dict | None] = {}

    out = []
    for p in props:
        if for_sale_id not in p["property-statuses"]:
            continue
        m = p.get("property_meta") or {}
        city_names = [cities.get(c, "") for c in p.get("property-cities", [])]
        type_names = [types.get(t, "") for t in p.get("property-types", [])]
        loc = str(m.get("REAL_HOMES_property_location") or "")  # a veces dict, a veces repr en texto
        lat = re.search(r"latitude'?\s*:\s*'?(-?\d+\.\d+)", loc)
        lng = re.search(r"longitude'?\s*:\s*'?(-?\d+\.\d+)", loc)
        imgs = str(m.get("REAL_HOMES_property_images") or "")
        photos = re.findall(r"'large':\s*\{[^{}]*?'url':\s*'([^']+)'", imgs)
        if not photos:
            photos = ["https://coldwellbankersancarlos.com/wp-content/uploads/" + f
                      for f in re.findall(r"'file':\s*'(\d{4}/\d{2}/[^']+)'", imgs)]
        size_m2, size_orig = parse_area(m.get("REAL_HOMES_property_size"), m.get("REAL_HOMES_property_size_postfix"), "sq ft")
        lot_m2, lot_orig = parse_area(m.get("REAL_HOMES_property_lot_size"), m.get("REAL_HOMES_property_lot_size_postfix"), "sq ft")
        address = clean_text(m.get("REAL_HOMES_property_address"))
        if address and OFFICE_ADDRESS.match(address):
            address = None
        contact = {"agency": "Coldwell Banker Encantado Realty", "agent": None, "phone": None, "email": None}
        agent_ids = re.findall(r"\d+", str(m.get("REAL_HOMES_agents") or ""))
        if agent_ids:
            aid = agent_ids[0]
            if aid not in agents_cache:
                try:
                    a = fetcher.get(f"{BASE}/agents/{aid}").json()
                    am = a.get("agent_meta") or a.get("meta") or {}
                    agents_cache[aid] = {"agent": htmllib.unescape(a["title"]["rendered"]),
                                         "phone": am.get("REAL_HOMES_mobile_number") or am.get("REAL_HOMES_office_number"),
                                         "email": am.get("REAL_HOMES_agent_email")}
                except Exception as e:  # noqa: BLE001
                    LAST_ERRORS.append(f"agente {aid}: {e}")
                    agents_cache[aid] = None
            if agents_cache[aid]:
                contact.update({k: v for k, v in agents_cache[aid].items() if v})
        sub_zone = next((c.title() for c in city_names if c and c.upper() != "SAN CARLOS"), None)
        rec = make_listing(
            source=SOURCE, source_url=p["link"], listing_id=str(p["id"]), scraped_at=now_iso(),
            title=htmllib.unescape(p["title"]["rendered"]),
            price=m.get("REAL_HOMES_property_price") or None,
            currency=_currency(m.get("REAL_HOMES_property_price_prefix"), m.get("REAL_HOMES_property_price_postfix")),
            property_type=_type(type_names),
            bedrooms=m.get("REAL_HOMES_property_bedrooms") or None,
            bathrooms=parse_baths(m.get("REAL_HOMES_property_bathrooms")),
            construction_m2=size_m2, land_m2=lot_m2,
            zone=sub_zone, address=address, description=_text(p["content"]["rendered"]),
            photos=photos, contact=contact,
            lat=lat.group(1) if lat else None, lng=lng.group(1) if lng else None,
            published_at=(p.get("date_gmt") + "Z") if p.get("date_gmt") else None,
            extra={"area_publicada": size_orig, "terreno_publicado": lot_orig,
                   "ciudades_sitio": city_names, "tipos_sitio": type_names},
        )
        rec["_in_san_carlos_search"] = bool(city_names) and not (NOT_SC_CITIES & {c.upper() for c in city_names})
        out.append(rec)
    return out, notes
