"""Deduplicación de la misma propiedad publicada en varios portales.

Criterio conservador (preferimos dejar un duplicado a fusionar dos casas
distintas):
  1. Nunca se fusionan dos anuncios de la MISMA fuente (en preventas hay
     unidades idénticas que son propiedades distintas).
  2. Candidatos: precio equivalente (±1.5 %, o ±4 % si están en monedas
     distintas) y al menos una superficie igual (±3 %), sin conflicto en
     recámaras/baños; o misma dirección con número.
  3. Confirmación: alguna foto visualmente igual (dHash), o bien coincidencia
     de zona + todas las superficies conocidas.
El registro fusionado conserva todos los anuncios en `sources` y anota de
qué portal salió cada dato rellenado (`field_sources`).
"""
from __future__ import annotations

import io
import json
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from difflib import SequenceMatcher
from itertools import combinations

from .common import Fetcher, slugify

FILLABLE = ("bedrooms", "bathrooms", "construction_m2", "land_m2", "zone", "address",
            "description", "property_type", "contact", "published_at")


def _close(a, b, tol):
    if a is None or b is None:
        return None
    if a == b:
        return True
    return abs(a - b) / max(a, b) <= tol


def _price_mxn(rec, usd_mxn):
    if rec.get("price") is None or rec.get("currency") is None:
        return None
    if rec["currency"] == "MXN":
        return rec["price"]
    return rec["price"] * usd_mxn if usd_mxn else None


def _norm_addr(s):
    if not s:
        return None
    s = slugify(s)
    return s if re.search(r"\d", s) and len(s) > 8 else None


CODE_RE = re.compile(r"^[A-Z]{2,6}-?[A-Z]{0,3}-?\d{3,6}$")


def _shared_code(a, b):
    """Clave de inmobiliaria (p. ej. SACV-6082 de NEXT) que un portal copia en la
    descripción del mismo anuncio publicado en otro portal."""
    for x, y in ((a, b), (b, a)):
        lid = (x.get("listing_id") or "").strip().upper()
        if lid and CODE_RE.match(lid):
            text = f"{y.get('title') or ''} {y.get('description') or ''} {y.get('listing_id') or ''}".upper()
            if re.search(rf"(?<![A-Z0-9-]){re.escape(lid)}(?![0-9])", text):
                return lid
    return None


def _completeness(rec):
    score = sum(rec.get(k) is not None for k in ("price", "bedrooms", "bathrooms", "construction_m2",
                                                   "land_m2", "zone", "address", "description"))
    score += min(len(rec.get("photos") or []), 10) / 5
    score += 3 if rec.get("coords_precision") == "exacta" else 0
    score += 2 if rec.get("currency") else 0
    return score


HASH_CACHE = Path(__file__).resolve().parent.parent / "data" / "photo_hash_cache.json"


class PhotoHasher:
    """dHash de fotos con caché en disco; las descargas se hacen en paralelo por
    sitio (un hilo por host, con pausa entre peticiones al mismo host)."""

    def __init__(self, log):
        self.fetchers: dict[str, Fetcher] = {}
        self.lock = threading.Lock()
        self.cache: dict[str, int | None] = {}
        if HASH_CACHE.exists():
            try:
                self.cache = {k: v for k, v in json.loads(HASH_CACHE.read_text()).items() if v is not None}
            except (json.JSONDecodeError, OSError):
                self.cache = {}
        self.log = log

    def save(self):
        HASH_CACHE.write_text(json.dumps({k: v for k, v in self.cache.items() if v is not None}))

    def prefetch(self, urls):
        todo = defaultdict(list)
        for u in dict.fromkeys(urls):
            if u not in self.cache:
                todo[urlparse(u).netloc].append(u)
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=min(12, len(todo))) as ex:
            list(ex.map(lambda us: [self.dhash(u) for u in us], todo.values()))
        self.save()

    def _fetcher(self, url):
        host = urlparse(url).netloc
        with self.lock:
            if host not in self.fetchers:
                self.fetchers[host] = Fetcher("fotos", delay=0.5, respect_robots=False)
            return self.fetchers[host]

    def dhash(self, url: str) -> int | None:
        if url in self.cache:
            return self.cache[url]
        h = None
        try:
            from PIL import Image
            r = self._fetcher(url).get(url)
            img = Image.open(io.BytesIO(r.content)).convert("L")
            # Recorte cuadrado central: tolera que un portal recorte la foto ("cover")
            w, h = img.size
            side = min(w, h)
            img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((9, 8))
            px = list(img.getdata())
            bits = 0
            for row in range(8):
                for col in range(8):
                    bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
            h = bits
        except Exception as e:  # noqa: BLE001
            self.log.append(f"dHash falló para {url[:90]}: {e}")
        self.cache[url] = h
        return h

    def any_match(self, a: list[str], b: list[str], n: int = 6) -> bool | None:
        ha = [h for h in (self.dhash(u) for u in a[:n]) if h is not None]
        hb = [h for h in (self.dhash(u) for u in b[:n]) if h is not None]
        if not ha or not hb:
            return None
        return any(bin(x ^ y).count("1") <= 8 for x in ha for y in hb)


def dedupe(listings: list[dict], usd_mxn: float | None, log: list) -> tuple[list[dict], dict]:
    n = len(listings)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    hasher = PhotoHasher(log)
    evidence: dict[tuple[int, int], str] = {}

    # Pre-descarga en paralelo las fotos de los pares que tienen precio compatible
    def _maybe_same(a, b):
        if a.get("price") is None or b.get("price") is None:
            return False
        if a["source"] == b["source"]:
            return a.get("price") == b.get("price") and bool(_norm_addr(a.get("address")))
        if a.get("currency") and b.get("currency"):
            return bool(_close(_price_mxn(a, usd_mxn), _price_mxn(b, usd_mxn), 0.04))
        return a.get("price") == b.get("price")
    need = set()
    for i, j in combinations(range(n), 2):
        if _maybe_same(listings[i], listings[j]):
            need.update((listings[i].get("photos") or [])[:6])
            need.update((listings[j].get("photos") or [])[:6])
    hasher.prefetch(sorted(need))
    for i, j in combinations(range(n), 2):
        a, b = listings[i], listings[j]
        if a["source"] == b["source"]:
            # Mismo portal: solo con evidencia fuerte (republicación del mismo inmueble)
            addr = _norm_addr(a.get("address"))
            if (addr and addr == _norm_addr(b.get("address")) and a.get("price") == b.get("price")
                    and a.get("construction_m2") == b.get("construction_m2")
                    and a.get("land_m2") == b.get("land_m2")
                    and hasher.any_match(a.get("photos") or [], b.get("photos") or [])):
                evidence[(i, j)] = "mismo portal: misma dirección, precio, m² y fotos (anuncio duplicado)"
                parent[find(j)] = find(i)
            continue
        reasons = []
        code = _shared_code(a, b)
        if code:
            evidence[(i, j)] = f"la clave del anuncio {code} aparece en ambos"
            parent[find(j)] = find(i)
            continue
        mls_a = (a.get("extra") or {}).get("mls_san_carlos")
        if mls_a and mls_a == (b.get("extra") or {}).get("mls_san_carlos"):
            evidence[(i, j)] = f"mismo número MLS San Carlos ({mls_a})"
            parent[find(j)] = find(i)
            continue
        same_ccy = a.get("currency") == b.get("currency")
        if a.get("currency") and b.get("currency"):
            pa, pb = _price_mxn(a, usd_mxn), _price_mxn(b, usd_mxn)
            price_ok = _close(pa, pb, 0.015 if same_ccy else 0.04)
        else:
            # Un anuncio sin moneda (p. ej. el MLS): solo cifra idéntica
            price_ok = a.get("price") is not None and a.get("price") == b.get("price")
        addr_a, addr_b = _norm_addr(a.get("address")), _norm_addr(b.get("address"))
        if addr_a and addr_a == addr_b and price_ok:
            reasons.append("misma dirección y precio")
        else:
            if not price_ok:
                continue
            m_c = _close(a.get("construction_m2"), b.get("construction_m2"), 0.03)
            m_l = _close(a.get("land_m2"), b.get("land_m2"), 0.03)
            # Campos cruzados (un portal captura construcción como terreno o viceversa)
            m_x = bool(_close(a.get("construction_m2"), b.get("land_m2"), 0.03) or _close(a.get("land_m2"), b.get("construction_m2"), 0.03))
            if a.get("bedrooms") and b.get("bedrooms") and a["bedrooms"] != b["bedrooms"]:
                continue
            photo = hasher.any_match(a.get("photos") or [], b.get("photos") or [], n=6)
            zone_sim = SequenceMatcher(None, slugify(a.get("zone") or ""), slugify(b.get("zone") or "")).ratio()
            if photo:
                # Misma foto + mismo precio (+ mismas recámaras si ambas las dicen): mismo inmueble.
                detail = "m² iguales" if (m_c or m_l) else "m² cruzados entre portales" if m_x else "m² distintos entre portales"
                reasons.append(f"precio + foto idéntica ({detail})")
            elif m_c and m_l and a.get("bedrooms") and a.get("bedrooms") == b.get("bedrooms") and zone_sim >= 0.8:
                reasons.append("precio + m² construcción + m² terreno + recámaras + zona (fotos no comparables)")
            else:
                continue
        evidence[(i, j)] = "; ".join(reasons)
        parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    hasher.save()
    out = []
    merged_count = 0
    for idxs in groups.values():
        recs = [listings[i] for i in idxs]
        recs.sort(key=_completeness, reverse=True)
        primary = dict(recs[0])
        primary["sources"] = [
            {k: r.get(k) for k in ("source", "source_url", "listing_id", "scraped_at", "price", "currency")}
            for r in recs
        ]
        if len(recs) > 1:
            merged_count += 1
            field_sources = {}
            for k in FILLABLE:
                if primary.get(k) is None:
                    for r in recs[1:]:
                        if r.get(k) is not None:
                            primary[k] = r[k]
                            field_sources[k] = r["source"]
                            break
            if primary.get("currency") is None:
                for r in recs[1:]:
                    if r.get("currency") and r.get("price") == primary.get("price"):
                        primary["currency"] = r["currency"]
                        field_sources["currency"] = r["source"]
                        break
            if primary.get("coords_precision") != "exacta":
                for r in recs[1:]:
                    if r.get("coords_precision") == "exacta":
                        primary["lat"], primary["lng"] = r["lat"], r["lng"]
                        primary["coords_precision"] = "exacta"
                        field_sources["coords"] = r["source"]
                        break
            if len(primary.get("photos") or []) < 3:
                for r in recs[1:]:
                    primary["photos"] = list(dict.fromkeys((primary.get("photos") or []) + (r.get("photos") or [])))
            primary["field_sources"] = field_sources or None
            primary["dedupe_evidence"] = sorted({evidence[p] for p in evidence if p[0] in idxs and p[1] in idxs})
        out.append(primary)
    return out, {"input": n, "output": len(out), "merged_groups": merged_count}
