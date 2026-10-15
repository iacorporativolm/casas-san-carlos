"""Infraestructura compartida por todos los scrapers.

- Cliente HTTP con User-Agent identificable, pausa mínima por host y
  verificación de robots.txt antes de cada petición.
- Detección de bloqueos anti-bot: si un sitio responde con un reto
  (captcha, Cloudflare, 403), se lanza BlockedError y la fuente se reporta
  como bloqueada. Nunca se intenta evadir la protección.
- Esquema único de propiedad (make_listing) con validación: sin
  source_url no hay propiedad.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

BOT_NAME = "SanCarlosCasasBot"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SanCarlosCasasBot/1.0; "
    "buscador personal no comercial de casas en San Carlos, Sonora)"
)
DEFAULT_DELAY = 2.5  # segundos mínimos entre peticiones al mismo host
TIMEOUT = 30

# Área de San Carlos Nuevo Guaymas (holgada). Coordenadas fuera de esta caja
# se descartan (se guardan como null) en vez de "corregirse".
# Área de San Carlos Nuevo Guaymas, verificada con OSM (Nominatim):
#   pueblo 27.969,-111.032 · Los Algodones -111.10 · Bahía Delfín -110.987 ·
#   corredor del Blvd. hacia la carretera (Villa Bemela) hasta -110.95.
# Se excluye la esquina sureste: Miramar/Las Tinajas (27.92,-110.94) y Guaymas.
# Coordenadas fuera del área se descartan (null) en vez de "corregirse".
SAN_CARLOS_BBOX = {"south": 27.88, "north": 28.08, "west": -111.20, "east": -110.95}
MIRAMAR_CORNER = {"lat_below": 27.955, "lng_above": -110.975}


class BlockedError(Exception):
    """El sitio bloqueó el acceso (anti-bot, 403, captcha)."""


class RobotsDisallowed(Exception):
    """robots.txt no permite la URL solicitada."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Fetcher:
    source: str
    delay: float = DEFAULT_DELAY
    extra_headers: dict = field(default_factory=dict)
    respect_robots: bool = True
    log: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
            }
        )
        self.session.headers.update(self.extra_headers)
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    # ---------------------------------------------------------------- robots
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base in self._robots:
            return self._robots[base]
        rp = urllib.robotparser.RobotFileParser()
        robots_url = base + "/robots.txt"
        try:
            self._wait(p.netloc)
            r = self.session.get(robots_url, timeout=TIMEOUT)
            self.requests_made += 1
            if r.status_code in (401, 403):
                # Convención de robotparser: acceso a robots.txt prohibido => todo prohibido
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
            crawl_delay = rp.crawl_delay(BOT_NAME) or rp.crawl_delay("*")
            if crawl_delay:
                self.delay = max(self.delay, float(crawl_delay))
        except requests.RequestException as e:
            self.log.append(f"robots.txt no disponible en {base}: {e}")
            rp = None
        self._robots[base] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(BOT_NAME, url)

    # ------------------------------------------------------------- requests
    def _wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_hit[host] = time.monotonic()

    def post(self, url: str, *, data: dict | None = None, files: dict | None = None,
             headers: dict | None = None) -> requests.Response:
        return self.get(url, headers=headers, _method="POST", _data=data, _files=files)

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            expect_json: bool = False, allow_404: bool = False, _method: str = "GET",
            _data: dict | None = None, _files: dict | None = None) -> requests.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        backoff = 5
        for attempt in range(3):
            self._wait(host)
            try:
                r = self.session.request(_method, url, params=params, headers=headers, data=_data,
                                         files=_files, timeout=TIMEOUT)
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                self.log.append(f"error de red {url}: {e}; reintento")
                time.sleep(backoff)
                backoff *= 2
                continue
            self.requests_made += 1
            if r.status_code == 404 and allow_404:
                return r
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                self.log.append(f"HTTP {r.status_code} en {url}; reintento en {backoff}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            if looks_blocked(r):
                raise BlockedError(f"HTTP {r.status_code} / reto anti-bot en {url}")
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")

    def get_html(self, url: str, **kw) -> str:
        r = self.get(url, **kw)
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
        return r.text

    def get_json(self, url: str, **kw) -> Any:
        return self.get(url, expect_json=True, **kw).json()


BLOCK_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "px-captcha",
    "captcha-delivery.com",
    "Access to this page has been denied",
    "Request unsuccessful. Incapsula",
    "_Incapsula_Resource",
    "Human Verification",
    "Security verification",
    "complete the following challenge",
    "Access Denied",
    "awswaf",
)


def looks_blocked(r: requests.Response) -> bool:
    if r.status_code in (401, 403, 405, 451):
        return True
    ctype = r.headers.get("content-type", "")
    if "html" in ctype and r.status_code in (200, 202, 503):
        head = r.text[:6000]
        return any(m in head for m in BLOCK_MARKERS)
    return False


# ------------------------------------------------------------------ parsing
def clean_text(s: str | None) -> str | None:
    if s is None:
        return None
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    s = s.strip()
    return s or None


def parse_number(s: Any) -> float | None:
    """'2,450,000' -> 2450000.0 ; '3.5' -> 3.5 ; None/'' -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip()
    m = re.search(r"\d[\d,\.]*", txt)
    if not m:
        return None
    num = m.group(0).rstrip(",.")
    # Formato mexicano/US: coma = miles, punto = decimal
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", num):  # 1.250.000 (estilo europeo)
        num = num.replace(".", "")
    elif re.fullmatch(r"\d+,\d{1,2}", num):  # 1312,50 (coma decimal)
        num = num.replace(",", ".")
    num = num.replace(",", "")
    num = num.rstrip(".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_baths(s: Any) -> float | None:
    """'3 1/2' -> 3.5 ; '2½' -> 2.5 ; '2.5' -> 2.5 ; '3' -> 3.0"""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).replace("½", " 1/2").strip()
    m = re.match(r"^\s*(\d+)\s+1/2", txt)
    if m:
        return int(m.group(1)) + 0.5
    if re.match(r"^\s*1/2\s*$", txt):
        return 0.5
    return parse_number(txt)


SQFT_TO_M2 = 0.09290304


def parse_area(value: Any, unit: str | None, default_unit: str = "m2") -> tuple[float | None, str | None]:
    """Superficie -> (m², texto original). Convierte pies² a m² (conversión de
    unidades, no estimación). `default_unit` = unidad que el sitio muestra
    cuando el anuncio no especifica una."""
    n = parse_number(value)
    if n is None or n <= 0:
        return None, None
    u = (unit or "").strip().lower()
    if not u:
        u = default_unit
    original = f"{value} {unit or default_unit}".strip()
    if re.search(r"f(ee)?t|pies|sq\.?\s*f|sg\s*ft", u):
        return round(n * SQFT_TO_M2, 1), original
    if re.search(r"acre", u):
        return round(n * 4046.8564224, 1), original
    if re.search(r"hect|\bha\b", u):
        return round(n * 10000, 1), original
    return n, original


def parse_int(s: Any) -> int | None:
    n = parse_number(s)
    return int(n) if n is not None else None


def detect_currency(text: str | None) -> str | None:
    if not text:
        return None
    t = text.upper()
    if "USD" in t or "US$" in t or "U$S" in t or "DLLS" in t or "DÓLARES" in t or "DOLARES" in t:
        return "USD"
    if "MXN" in t or "MN" in t.split() or "PESOS" in t or "M.N." in t:
        return "MXN"
    return None


SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "en"}


def smart_case(s: str) -> str:
    """Solo cambia mayúsculas/minúsculas de nombres de colonia escritos TODO EN
    MAYÚSCULAS o "Title Case" mecánico, para agrupar zonas iguales."""
    letters = [c for c in s if c.isalpha()]
    if not letters or not (all(c.isupper() for c in letters) or re.search(r"\b(De|Del|La|Las|Los)\b", s)):
        return s
    words = s.lower().split(" ")
    roman = re.compile(r"^(i|ii|iii|iv|v|vi|vii|viii|ix|x)$")
    return " ".join(w.upper() if roman.match(w) else w if (i and w in SMALL_WORDS) else (w[:1].upper() + w[1:])
                    for i, w in enumerate(words))


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def norm_type(raw: str | None) -> str | None:
    """Normaliza el tipo de propiedad a un vocabulario corto en español."""
    if not raw:
        return None
    t = slugify(raw)
    if any(k in t for k in ("terreno", "lote", "lot", "land")):
        return "Terreno"
    if any(k in t for k in ("departamento", "condo", "apartment", "depto", "flat")):
        return "Departamento / Condominio"
    if any(k in t for k in ("townhouse", "town-house", "villa", "duplex", "casa-en-condominio")):
        return "Townhouse / Villa"
    if any(k in t for k in ("casa", "house", "home", "residencia", "residence", "single-family", "bungalow")):
        return "Casa"
    if any(k in t for k in ("local", "oficina", "bodega", "comercial", "commercial", "edificio", "hotel", "nave")):
        return "Comercial"
    if any(k in t for k in ("rancho", "quinta", "ranch")):
        return "Rancho / Quinta"
    return clean_text(raw)


RESIDENTIAL_TYPES = {"Casa", "Departamento / Condominio", "Townhouse / Villa"}


def in_san_carlos_bbox(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    b = SAN_CARLOS_BBOX
    if not (b["south"] <= lat <= b["north"] and b["west"] <= lng <= b["east"]):
        return False
    c = MIRAMAR_CORNER
    return not (lat < c["lat_below"] and lng > c["lng_above"])


def abs_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base, href)


def extract_json_ld(soup) -> list[dict]:
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "null")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                out.extend(d for d in data["@graph"] if isinstance(d, dict))
            out.append(data)
    return out


# ------------------------------------------------------------------- schema
LISTING_FIELDS = (
    "title", "price", "currency", "property_type", "bedrooms", "bathrooms",
    "construction_m2", "land_m2", "zone", "address", "description", "photos",
    "contact", "lat", "lng", "coords_precision", "published_at",
)


def make_listing(*, source: str, source_url: str, listing_id: str | None = None,
                 scraped_at: str | None = None, **fields) -> dict:
    """Crea un registro de propiedad. Campos ausentes => None (nunca se adivinan)."""
    if not source_url or not source_url.startswith("http"):
        raise ValueError("Toda propiedad necesita source_url")
    unknown = set(fields) - set(LISTING_FIELDS) - {"extra"}
    if unknown:
        raise ValueError(f"Campos desconocidos: {unknown}")
    rec: dict[str, Any] = {k: fields.get(k) for k in LISTING_FIELDS}
    rec["source"] = source
    rec["source_url"] = source_url
    rec["listing_id"] = str(listing_id) if listing_id not in (None, "") else None
    rec["scraped_at"] = scraped_at or now_iso()

    # Normalizaciones sin inventar valores
    for k in ("title", "description", "zone", "address"):
        rec[k] = clean_text(rec[k])
    if rec["zone"]:
        rec["zone"] = smart_case(rec["zone"])
    rec["price"] = parse_number(rec["price"]) if rec["price"] is not None else None
    if rec["price"] is not None and rec["price"] <= 0:
        rec["price"] = None
    if rec["currency"] not in (None, "MXN", "USD"):
        raise ValueError(f"Moneda no soportada: {rec['currency']}")
    if rec["price"] is None:
        rec["currency"] = rec["currency"]  # se conserva si el anuncio la dice
    for k in ("bedrooms", "bathrooms", "construction_m2", "land_m2"):
        v = rec[k]
        v = parse_number(v) if v is not None else None
        rec[k] = v if (v is not None and v > 0) else None
    if rec["bedrooms"] is not None:
        rec["bedrooms"] = int(rec["bedrooms"]) if float(rec["bedrooms"]).is_integer() else rec["bedrooms"]
    photos = [p for p in (rec["photos"] or []) if isinstance(p, str) and p.startswith("http")]
    rec["photos"] = list(dict.fromkeys(photos))
    rec["contact"] = rec["contact"] or None
    pa = rec.get("published_at")
    if isinstance(pa, str):
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}:\d{2}))?", pa)
        rec["published_at"] = (m.group(1) + (f"T{m.group(2)}Z" if m.group(2) and pa.endswith("Z") else "")) if m else None
    rec["property_type"] = norm_type(rec["property_type"])
    # Coordenadas: solo si vienen del anuncio y caen en San Carlos
    lat, lng = rec["lat"], rec["lng"]
    try:
        lat = float(lat) if lat not in (None, "") else None
        lng = float(lng) if lng not in (None, "") else None
    except (TypeError, ValueError):
        lat = lng = None
    if lat is not None and lng is not None and in_san_carlos_bbox(lat, lng):
        rec["lat"], rec["lng"] = lat, lng
        rec["coords_precision"] = rec["coords_precision"] or "exacta"
    else:
        if lat is not None or lng is not None:
            rec.setdefault("_notes", []).append(
                f"coordenadas del anuncio ({lat},{lng}) fuera de San Carlos; descartadas"
            )
        rec["lat"] = rec["lng"] = None
        rec["coords_precision"] = None
    if fields.get("extra"):
        rec["extra"] = fields["extra"]
    return rec
