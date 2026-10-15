"""Geocodificación de propiedades sin coordenadas con Nominatim (OpenStreetMap).

Política de Nominatim: máx. 1 petición/segundo, User-Agent identificable y
caché local. Solo se geocodifica la colonia/zona (o la dirección si no hay
zona) y el resultado se marca SIEMPRE como coords_precision = "aproximada".
Si la única ubicación conocida es "San Carlos" (todo el pueblo), no se
asignan coordenadas: sería un pin engañoso.
"""
from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

from .common import Fetcher, in_san_carlos_bbox, slugify, SAN_CARLOS_BBOX

NOMINATIM = "https://nominatim.openstreetmap.org/search"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "geocode_cache.json"

# Nombres que solo identifican el pueblo completo (no una colonia)
TOWN_ONLY = {
    "san-carlos", "san-carlos-nuevo-guaymas", "nuevo-guaymas", "guaymas",
    "san-carlos-guaymas", "sonora", "san-carlos-sonora", "guaymas-sonora",
    "san-carlos-nuevo-guaymas-guaymas", "san-carlos-son",
}


def _cache_load() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _cache_save(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


TOWN_WORDS = r"(san carlos( nuevo guaymas)?|nuevo guaymas|guaymas|sonora|son\.?|m[eé]xico|c\.?p\.?\s*\d{5}|\d{5})"


def _strip_town(zone: str) -> str:
    """Quita el pueblo/estado/CP como componentes sueltos, pero conserva nombres
    de colonia que lo contienen ("Villas de San Carlos", "Lomas de San Carlos")."""
    parts = [p.strip() for p in re.split(r"[,/|]", zone) if p.strip()]
    keep = []
    for p in parts:
        if re.fullmatch(rf"(?i)({TOWN_WORDS}[\s.]*)+", p):
            continue
        # "Country Club San Carlos" -> "Country Club"; "Lomas de San Carlos" se queda
        p2 = re.sub(r"(?i)(?<!\bde)(?<!\bdel)\s+san carlos( nuevo guaymas)?\s*$", "", p)
        p2 = re.sub(r"(?i)\s+(en\s+)?(nuevo guaymas|guaymas|sonora|son\.?)\s*$", "", p2)
        keep.append(p2)
    z = " ".join(keep)
    z = re.sub(r"(?i)\b(c\.?p\.?\s*)?\d{5}\b", " ", z)
    return re.sub(r"\s+", " ", z).strip()


def zone_key(zone: str | None) -> str:
    """Clave normalizada de colonia para agrupar anuncios de la misma zona."""
    if not zone:
        return ""
    k = slugify(_strip_town(zone))
    k = GENERIC_ANY.sub("", k)
    return k.strip("-")


GENERIC = re.compile(r"^(fraccionamiento|fracc|colonia|col|condominio|condominios|residencial|privada|sector)-")
GENERIC_ANY = re.compile(r"(^|-)(fraccionamiento|fracc|colonia|col|condominios?|privada|sector|en)(?=-|$)")


def _name_matches(core_slug: str, result: dict) -> bool:
    """El resultado debe llamarse como la colonia buscada (evita que "Bahía"
    caiga en "Condominio Bahía Delfín")."""
    name = slugify(result.get("name") or result.get("display_name", "").split(",")[0])
    a, b = GENERIC.sub("", core_slug), GENERIC.sub("", name)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return shorter in longer and len(shorter) / len(longer) >= 0.7 or SequenceMatcher(None, a, b).ratio() >= 0.85


def _radius_from_bbox(bb: list[str]) -> int:
    s, n, w, e = map(float, bb)
    dy = (n - s) * 111_000
    dx = (e - w) * 111_000 * math.cos(math.radians((n + s) / 2))
    r = int(max(dx, dy) / 2)
    return max(150, min(r, 1500))


def geocode_zone(fetcher: Fetcher, zone: str, cache: dict) -> dict | None:
    core = _strip_town(zone)
    key = slugify(core)
    if not key or key in TOWN_ONLY:
        return None
    if key in cache:
        return cache[key]
    b = SAN_CARLOS_BBOX
    params = {
        "q": f"{core}, San Carlos, Guaymas, Sonora, México",
        "format": "jsonv2",
        "limit": 3,
        "countrycodes": "mx",
        "viewbox": f"{b['west']},{b['north']},{b['east']},{b['south']}",
        "bounded": 1,
    }
    results = fetcher.get_json(NOMINATIM, params=params)
    if not results:
        # Segundo intento: sin "San Carlos" (algunas colonias están mapeadas solo con su nombre)
        params["q"] = f"{core}, Sonora, México"
        results = fetcher.get_json(NOMINATIM, params=params)
    hit = None
    for r in results or []:
        lat, lng = float(r["lat"]), float(r["lon"])
        if in_san_carlos_bbox(lat, lng) and _name_matches(key, r):
            hit = {
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "radius_m": _radius_from_bbox(r.get("boundingbox", [lat, lat, lng, lng])),
                "osm_display_name": r.get("display_name"),
                "query": params["q"],
            }
            break
    cache[key] = hit
    _cache_save(cache)
    return hit


def _haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _corpus_index(listings: list[dict]) -> dict:
    """colonia -> coordenadas EXACTAS de otros anuncios reales de esa colonia."""
    idx: dict[str, list] = {}
    for r in listings:
        if r.get("coords_precision") == "exacta":
            k = zone_key(r.get("zone"))
            if k:
                idx.setdefault(k, []).append((r["lat"], r["lng"], r["source"]))
    out = {}
    for k, pts in idx.items():
        if len(pts) < 2:
            continue
        lats = sorted(p[0] for p in pts)
        lngs = sorted(p[1] for p in pts)
        med = (lats[len(lats) // 2], lngs[len(lngs) // 2])
        spread = max(_haversine_m(med, p) for p in pts)
        if spread <= 1500:  # colonia compacta: el centro representa la zona
            out[k] = {"lat": round(med[0], 6), "lng": round(med[1], 6), "n": len(pts),
                      "radius_m": int(max(200, min(spread, 1500))),
                      "sources": sorted({p[2] for p in pts})}
    return out


def _corpus_lookup(idx: dict, texts: list[str]) -> tuple[str, dict] | None:
    for t in texts:
        k = zone_key(t)
        if not k:
            continue
        if k in idx:
            return k, idx[k]
        # Colonia conocida contenida en el texto ("... FRACCIONAMIENTO COUNTRY CLUB")
        hits = [c for c in idx if re.search(rf"(^|-){re.escape(c)}($|-)", k)]
        if hits:
            best = max(hits, key=len)
            return best, idx[best]
    return None


def geocode_all(listings: list[dict], log: list) -> dict:
    """Añade coordenadas APROXIMADAS (nivel colonia) a los anuncios sin coordenadas.
    1) Nominatim/OpenStreetMap por nombre de colonia (el nombre del resultado debe coincidir).
    2) Si OSM no tiene la colonia: mediana de las coordenadas exactas de otros anuncios
       reales de la misma colonia (mínimo 2, todos a ≤1.5 km de la mediana)."""
    fetcher = Fetcher("Nominatim", delay=1.2, respect_robots=False)  # API: rige su política de uso, no robots
    cache = _cache_load()
    corpus = _corpus_index(listings)
    stats = {"geocoded_osm": 0, "geocoded_corpus": 0, "not_found": 0, "town_only_or_missing": 0, "errors": 0}
    for rec in listings:
        if rec.get("lat") is not None:
            continue
        candidates = [c for c in (rec.get("zone"), rec.get("address")) if c]
        got = None
        try:
            for c in candidates:
                got = geocode_zone(fetcher, c, cache)
                if got:
                    break
        except Exception as e:  # noqa: BLE001 — reportamos y seguimos
            stats["errors"] += 1
            log.append(f"geocode error ({rec.get('zone')}): {e}")
        if got:
            rec["lat"], rec["lng"] = got["lat"], got["lng"]
            rec["coords_precision"] = "aproximada"
            rec["approx_radius_m"] = got["radius_m"]
            rec["geocode"] = {"service": "Nominatim / OpenStreetMap", "query": got["query"],
                              "match": got["osm_display_name"]}
            stats["geocoded_osm"] += 1
            continue
        hit = _corpus_lookup(corpus, candidates)
        if hit:
            k, c = hit
            rec["lat"], rec["lng"] = c["lat"], c["lng"]
            rec["coords_precision"] = "aproximada"
            rec["approx_radius_m"] = c["radius_m"]
            rec["geocode"] = {"service": f"Centro de {c['n']} anuncios con coordenadas exactas en la misma colonia",
                              "query": k, "match": ", ".join(c["sources"])}
            stats["geocoded_corpus"] += 1
        elif not candidates or all(not zone_key(c) for c in candidates):
            stats["town_only_or_missing"] += 1
        else:
            stats["not_found"] += 1
    return stats
