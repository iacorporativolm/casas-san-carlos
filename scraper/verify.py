"""Verificación de data/listings.json contra las fuentes reales.

1. Ninguna propiedad (ni ninguna entrada de `sources`) sin source_url.
2. Todos los source_url responden (sin 4xx/5xx, sin redirección a la portada
   o a una búsqueda, que es como los portales marcan un anuncio retirado).
3. Muestra aleatoria de N anuncios: se descarga la página del anuncio y se
   comprueba, de forma independiente al parser del scraper, que el precio y
   las recámaras guardados aparecen en el texto de la página.

Uso: python3 -m scraper.verify [--sample 10] [--seed 42] [--skip-links]
Escribe data/verify_report.json.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .common import BlockedError, Fetcher, RobotsDisallowed, now_iso

DATA = Path(__file__).resolve().parent.parent / "data"
ROOT = DATA.parent
# Sitios cuya ficha se pinta con JavaScript: se lee el texto visible renderizado
RENDER_HOSTS = {"www.remax.com.mx", "century21mexico.com"}


def rendered_text(url: str) -> str:
    import subprocess
    out = subprocess.run(["node", str(ROOT / "tests" / "render_text.js"), url], capture_output=True,
                         text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:200])
    return re.sub(r"\s+", " ", out.stdout)


def price_patterns(price: float) -> list[str]:
    p = int(round(price))
    pats = [f"{p:,}", f"{p:,}".replace(",", "."), str(p), f"{p:,}".replace(",", " "),
            f"{price:,.2f}", f"{int(price):,}"]
    if p >= 1_000_000 and p % 10_000 == 0:
        m = p / 1_000_000
        pats.append(f"{m:g}M")
        pats.append(f"{m:g} M")
        pats.append(f"{m:g} millones")
    return pats


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    meta = " ".join(m.get("content", "") for m in soup.find_all("meta") if m.get("content"))
    scripts = " ".join(s.get_text(" ") for s in soup.find_all("script", type="application/ld+json"))
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ") + " " + meta + " " + scripts)


def excerpt(text: str, rec: dict) -> str:
    """Fragmento de la página alrededor del precio, para revisión humana."""
    if rec.get("price") is not None:
        for p in price_patterns(rec["price"]):
            i = text.find(p)
            if i >= 0:
                return text[max(0, i - 200): i + 300]
    return text[:400]


def beds_in_text(beds, text: str) -> bool:
    b = int(beds) if float(beds).is_integer() else beds
    pat = rf"\b{b}\s*(rec[aá]maras?|recs?\.?|habitaci[oó]n(es)?|dormitorios?|cuartos?|bed(room)?s?|beds?|br)\b"
    pat2 = rf"(rec[aá]maras?|habitaciones|dormitorios|cuartos|bedrooms?|beds?)\s*[:\-|]?\s*{b}\b"
    return bool(re.search(pat, text, re.I) or re.search(pat2, text, re.I))


def looks_removed(orig: str, final: str) -> bool:
    po, pf = urlparse(orig), urlparse(final)
    if po.netloc.replace("www.", "") != pf.netloc.replace("www.", ""):
        return True
    path = pf.path.rstrip("/")
    return path in ("", "/es", "/en") or any(k in pf.path for k in ("/search", "/busqueda", "/resultados"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--skip-links", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads((DATA / "listings.json").read_text(encoding="utf-8"))
    listings = data["listings"]
    report = {"run_at": now_iso(), "total": len(listings)}

    # 1. source_url obligatorio
    missing = [r.get("id") for r in listings if not r.get("source_url")]
    missing += [r.get("id") for r in listings for s in r.get("sources", []) if not s.get("source_url")]
    report["missing_source_url"] = missing
    print(f"Propiedades sin source_url: {len(missing)}")

    fetchers: dict[str, Fetcher] = {}
    import threading
    lock = threading.Lock()

    def fetch(url):
        host = urlparse(url).netloc
        with lock:
            f = fetchers.get(host)
            if f is None:
                # Mismo ritmo que el scraper de esa fuente (p. ej. Crawl-Delay 20 s)
                delay = 20.0 if "remax-firstchoice" in host else 2.5
                f = fetchers[host] = Fetcher(host, delay=delay)
        return f.get(url, allow_404=True)

    def check_url(u):
        try:
            r = fetch(u)
            if r.status_code >= 400:
                return {"url": u, "problem": f"HTTP {r.status_code}"}
            if looks_removed(u, r.url):
                return {"url": u, "problem": f"redirige a {r.url} (anuncio posiblemente retirado)"}
        except (BlockedError, RobotsDisallowed) as e:
            return {"url": u, "problem": f"no verificable: {e}", "unverifiable": True}
        except Exception as e:  # noqa: BLE001
            return {"url": u, "problem": f"{type(e).__name__}: {e}"}
        return None

    # 2. links rotos (un hilo por sitio; dentro de cada sitio, secuencial con pausa)
    broken = []
    if not args.skip_links:
        from collections import defaultdict
        from concurrent.futures import ThreadPoolExecutor
        urls = sorted({s["source_url"] for r in listings for s in r.get("sources", [])} | {r["source_url"] for r in listings})
        by_host = defaultdict(list)
        for u in urls:
            by_host[urlparse(u).netloc].append(u)
        print(f"Revisando {len(urls)} URLs en {len(by_host)} sitios ...", flush=True)
        with ThreadPoolExecutor(max_workers=len(by_host) or 1) as ex:
            for res in ex.map(lambda us: [x for x in map(check_url, us) if x], by_host.values()):
                broken.extend(res)
        report["links_checked"] = len(urls)
    unverifiable = [b for b in broken if b.get("unverifiable")]
    broken = [b for b in broken if not b.get("unverifiable")]
    report["broken_links"] = broken
    report["unverifiable_links"] = unverifiable
    print(f"Links rotos: {len(broken)} | no verificables (el sitio pidió verificación humana / bloqueó): {len(unverifiable)}")

    # 3. muestra aleatoria: se sacan anuncios al azar hasta tener N verificados;
    #    los que el sitio no deja abrir (reto anti-bot) se reportan aparte.
    rnd = random.Random(args.seed)
    order = rnd.sample(listings, len(listings))
    checks, skipped = [], []
    for rec in order:
        if len(checks) >= args.sample:
            break
        item = {"id": rec["id"], "source": rec["source"], "url": rec["source_url"],
                "stored_price": rec.get("price"), "stored_currency": rec.get("currency"),
                "stored_bedrooms": rec.get("bedrooms")}
        try:
            r = fetch(rec["source_url"])
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = "utf-8"
            item["http_status"] = r.status_code
            if urlparse(rec["source_url"]).netloc in RENDER_HOSTS:
                text = rendered_text(rec["source_url"])
                item["read_with"] = "Chromium (página renderizada)"
            else:
                text = page_text(r.text)
                item["read_with"] = "HTML"
            item["page_excerpt"] = excerpt(text, rec)
            item["price_found"] = any(p in text for p in price_patterns(rec["price"])) if rec.get("price") is not None else None
            item["bedrooms_found"] = beds_in_text(rec["bedrooms"], text) if rec.get("bedrooms") is not None else None
        except (BlockedError, RobotsDisallowed) as e:
            skipped.append({"url": rec["source_url"], "source": rec["source"], "reason": str(e)[:160]})
            print(f"  [N/V] {rec['source']:<28} no verificable hoy: {str(e)[:90]}")
            continue
        except Exception as e:  # noqa: BLE001
            item["error"] = f"{type(e).__name__}: {e}"
        checks.append(item)
        flag = "OK " if item.get("price_found") is not False and item.get("bedrooms_found") is not False and "error" not in item else "REV"
        print(f"  [{flag}] {rec['source']:<28} precio={rec.get('price')} {rec.get('currency')} "
              f"(en página: {item.get('price_found')}) rec={rec.get('bedrooms')} (en página: {item.get('bedrooms_found')}) {rec['source_url']}")
    report["sample_skipped_unverifiable"] = skipped
    report["sample_checks"] = checks
    (DATA / "verify_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Reporte: data/verify_report.json")


if __name__ == "__main__":
    main()
