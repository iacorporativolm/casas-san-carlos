"""CENTURY 21 México (century21mexico.com).

robots.txt solo prohíbe /sisteconline/ y /mX/.
El buscador del sitio pide sus resultados como JSON agregando ?json=true a la
misma URL de resultados (es lo que hace su propio frontend):
  /v/resultados/operacion_venta/en-pais_mexico/en-estado_sonora/en-municipio_guaymas?json=true
San Carlos no es municipio: está dentro de Guaymas. Se abre la ficha de cada
inmueble residencial (/propiedad/<id>_<slug>?json=true) para descripción,
asesor, oficina, fotos y coordenadas del anuncio.
"""
from __future__ import annotations

from ..common import clean_text, make_listing, now_iso, parse_number

SOURCE = "CENTURY 21 México"
SITE = "https://century21mexico.com/"
LIST = "https://century21mexico.com/v/resultados/operacion_venta/en-pais_mexico/en-estado_sonora/en-municipio_guaymas"
DELAY = 2.5
LAST_ERRORS: list[str] = []
SUBTYPES = {"casa": "Casa", "departamento": "Departamento / Condominio", "casa_en_condominio": "Townhouse / Villa",
            "villa": "Townhouse / Villa", "townhouse": "Townhouse / Villa", "duplex": "Townhouse / Villa",
            "terreno": "Terreno", "lote_residencial": "Terreno"}
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _photo(f):
    if isinstance(f, dict):
        return f.get("large") or f.get("large1") or f.get("original") or f.get("medium")
    return f if isinstance(f, str) else None


def scrape(fetcher):
    LAST_ERRORS.clear()
    results, page = [], 1
    while True:
        url = LIST if page == 1 else f"{LIST}/pagina_{page}"
        d = fetcher.get_json(url, params={"json": "true"}, headers=XHR)
        batch = d.get("results") or []
        new = [r for r in batch if r["id"] not in {x["id"] for x in results}]
        results.extend(new)
        total = int(str(d.get("totalHits") or "0").strip() or 0)
        if not new or len(results) >= total or page >= 20:
            break
        page += 1
    notes = [f"{len(results)} inmuebles en venta en el municipio de Guaymas"]
    out = []
    for r in results:
        ptype = SUBTYPES.get(r.get("tipoPropiedad"))
        if ptype not in ("Casa", "Departamento / Condominio", "Townhouse / Villa"):
            continue
        detail_url = "https://century21mexico.com" + r["urlCorrectaPropiedad"]
        try:
            dd = fetcher.get_json(detail_url, params={"json": "true"}, headers=XHR)
        except Exception as e:  # noqa: BLE001
            LAST_ERRORS.append(f"ficha {detail_url}: {e}")
            continue
        e = dd.get("entity") or {}
        source_url = dd.get("canonical") or detail_url
        photos = [p for p in (_photo(f) for f in (dd.get("fotos") or [])) if p]
        agent = " ".join(x for x in (e.get("usname"), e.get("apellidoP"), e.get("apellidoM")) if x) or None
        lat = e.get("lat") if str(e.get("ocultarMapaInternet")) in ("0", "False", "None") else None
        lng = e.get("lon") if lat else None
        half = parse_number(e.get("mediosBanios"))
        rec = make_listing(
            source=SOURCE, source_url=source_url, listing_id=e.get("id") or r["id"], scraped_at=now_iso(),
            title=e.get("encabezado") or r.get("encabezado"),
            price=e.get("precio") or r.get("precio"), currency=e.get("moneda") or r.get("moneda"),
            property_type=SUBTYPES.get(e.get("subtipoPropiedad"), ptype),
            bedrooms=e.get("recamaras"), bathrooms=parse_number(e.get("banios")),
            construction_m2=e.get("m2C"), land_m2=e.get("m2T"),
            zone=e.get("colonia") or r.get("colonia"),
            address=e.get("direccionFormat"), description=clean_text(e.get("descripcion")),
            photos=photos, lat=lat, lng=lng,
            contact={"agency": f"CENTURY 21 {e.get('nombreOfna')}" if e.get("nombreOfna") else SOURCE,
                     "agent": agent, "phone": e.get("usuatelMovil") or e.get("telefonoOfna"),
                     "email": e.get("usuaemail") or e.get("emailOfna")},
            published_at=r.get("fechaAlta"),  # fecha de alta del anuncio en C21
            extra={"medios_banos": half, "mls": e.get("MLSNumber"), "cp": e.get("cP")},
        )
        rec["_in_san_carlos_search"] = False  # se decide por coordenadas o texto (zona/título)
        out.append(rec)
    return out, notes
