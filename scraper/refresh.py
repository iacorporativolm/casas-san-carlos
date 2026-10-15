"""Corre todo el pipeline: scrapers -> filtro San Carlos -> geocodificación ->
deduplicación -> tipo de cambio -> data/listings.json + data/sources_report.json.

Uso:
    python3 -m scraper.refresh                 # todas las fuentes
    python3 -m scraper.refresh --only lamudi,coldwell_sancarlos
    python3 -m scraper.refresh --skip-geocode --skip-photo-dedupe
Con --only, las demás fuentes se toman de su última corrida exitosa
(data/raw/<fuente>.json), conservando su scraped_at original.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import time
import traceback
from pathlib import Path

from .common import (RESIDENTIAL_TYPES, BlockedError, Fetcher, RobotsDisallowed, smart_case,
                     in_san_carlos_bbox, now_iso, slugify)
from .sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"

SAN_CARLOS_WORDS = ("san-carlos", "nuevo-guaymas", "tetakawi", "algodones", "marina-real",
                    "caracol", "bahia-el-encanto", "la-manga", "las-tinajas", "lomas-de-cortez")
FRACTIONAL = re.compile(r"(?i)venta fraccional|inter[eé]s fraccional|tiempo compartido|fractional (ownership|interest|sale)|time ?share")
NOT_SAN_CARLOS = ("miramar", "guaymas-centro", "empalme", "hermosillo", "san-carlos-nuevo-leon",
                  "san-carlos-california", "baja-california", "san-pedro-garza")


def is_san_carlos(rec: dict) -> bool:
    """Decide con datos del propio anuncio: coordenadas dentro de San Carlos,
    o título/zona/dirección que lo digan, o la clasificación de la fuente."""
    if rec.get("coords_precision") == "exacta" and in_san_carlos_bbox(rec["lat"], rec["lng"]):
        return True
    text = slugify(" ".join(filter(None, [rec.get("title"), rec.get("zone"), rec.get("address")])))
    if any(w in text for w in NOT_SAN_CARLOS):
        return False
    return any(w in text for w in SAN_CARLOS_WORDS) or bool(rec.get("_in_san_carlos_search"))


def run_source(key: str, mod) -> tuple[list[dict], dict]:
    started = time.monotonic()
    fetcher = Fetcher(mod.SOURCE, delay=getattr(mod, "DELAY", 2.5))
    rep = {"key": key, "source": mod.SOURCE, "site": mod.SITE, "started_at": now_iso(),
           "status": "ok", "raw_count": 0, "errors": [], "notes": []}
    listings: list[dict] = []
    try:
        listings, notes = mod.scrape(fetcher)
        rep["notes"].extend(notes or [])
        if not listings:
            rep["status"] = "sin_inventario"
    except BlockedError as e:
        rep["status"] = "bloqueado"
        rep["errors"].append(str(e))
    except RobotsDisallowed as e:
        rep["status"] = "robots_txt_no_permite"
        rep["errors"].append(f"robots.txt no permite: {e}")
    except Exception as e:  # noqa: BLE001
        rep["status"] = "error"
        rep["errors"].append(f"{type(e).__name__}: {e}")
        rep["traceback"] = traceback.format_exc(limit=4)
    # Un scraper puede devolver resultados parciales y además errores de fichas sueltas
    rep["errors"].extend(getattr(mod, "LAST_ERRORS", []) or [])
    if rep["status"] == "ok" and getattr(mod, "PARTIAL_BLOCK", False):
        rep["status"] = "parcial_bloqueado"
        rep["notes"].append("El sitio empezó a pedir verificación humana; se conservan solo las fichas leídas antes")
    rep["log"] = fetcher.log[-30:]
    rep["requests"] = fetcher.requests_made
    rep["duration_s"] = round(time.monotonic() - started, 1)
    rep["raw_count"] = len(listings)
    return listings, rep


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="fuentes separadas por coma (claves de scraper/sources)")
    ap.add_argument("--skip-geocode", action="store_true")
    ap.add_argument("--skip-photo-dedupe", action="store_true")
    args = ap.parse_args(argv)

    RAW.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    run_started = now_iso()
    reports = []
    all_raw: list[dict] = []

    to_run = []
    for key, modname in SOURCES.items():
        mod = importlib.import_module(f".sources.{modname}", __package__)
        raw_path = RAW / f"{key}.json"
        if only and key not in only:
            if raw_path.exists():
                prev = json.loads(raw_path.read_text(encoding="utf-8"))
                all_raw.extend(prev["listings"])
                prev["report"]["reused_from_previous_run"] = True
                reports.append(prev["report"])
            continue
        to_run.append((key, mod, raw_path))

    # Una fuente por hilo: cada una usa su propio host y su propia pausa entre
    # peticiones, así que la cortesía por sitio se mantiene.
    from concurrent.futures import ThreadPoolExecutor
    print(f"→ Corriendo {len(to_run)} fuentes en paralelo: {', '.join(m.SOURCE for _, m, _ in to_run)}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, len(to_run))) as ex:
        futures = [(key, mod, raw_path, ex.submit(run_source, key, mod)) for key, mod, raw_path in to_run]
        for key, mod, raw_path, fut in futures:
            listings, rep = fut.result()
            print(f"   {mod.SOURCE}: {rep['status']}, {rep['raw_count']} anuncios en {rep['duration_s']}s"
                  + (f" | {rep['errors'][0][:120]}" if rep["errors"] else ""), flush=True)
            if rep["status"] in ("ok", "sin_inventario", "parcial_bloqueado"):
                raw_path.write_text(json.dumps({"report": rep, "listings": listings}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
            all_raw.extend(listings)
            reports.append(rep)

    # ---------- fuentes no utilizables: sondeo en vivo de su estado
    probes_path = RAW / "_sondeos_fuentes_no_utilizables.json"
    if not only or "unavailable" in only:
        from .sources.unavailable import probe_all
        print("→ Sondeando fuentes bloqueadas/no permitidas ...", flush=True)
        probes = probe_all([])
        probes_path.write_text(json.dumps(probes, ensure_ascii=False, indent=1), encoding="utf-8")
        reports.extend(probes)
    elif probes_path.exists():
        for p in json.loads(probes_path.read_text(encoding="utf-8")):
            p["reused_from_previous_run"] = True
            reports.append(p)

    # Normaliza mayúsculas de colonias también en corridas reutilizadas
    for rec in all_raw:
        if rec.get("zone"):
            rec["zone"] = smart_case(rec["zone"])

    # ---------- coordenadas "por defecto": el mismo punto exacto repetido en
    # anuncios de colonias distintas de una misma fuente no es una ubicación real.
    from collections import defaultdict
    groups = defaultdict(list)
    for rec in all_raw:
        if rec.get("coords_precision") == "exacta":
            groups[(rec["source"], round(rec["lat"], 5), round(rec["lng"], 5))].append(rec)
    for (src, lat, lng), recs in groups.items():
        zones = {slugify(r.get("zone") or r.get("title") or "") for r in recs}
        if len(recs) >= 2 and len(zones) >= 2:
            for r in recs:
                r["lat"] = r["lng"] = None
                r["coords_precision"] = None
                r.setdefault("_notes", []).append(f"coordenada {lat},{lng} repetida en {len(recs)} anuncios de zonas distintas (valor por defecto del portal); descartada")
            next(x for x in reports if x["source"] == src)["notes"].append(
                f"Coordenada por defecto {lat},{lng} descartada en {len(recs)} anuncios")

    # ---------- filtro: San Carlos + venta residencial
    kept = []
    by_src = {r["source"]: r for r in reports}
    for rep in reports:
        rep.update(excluded_not_san_carlos=0, excluded_type=0, kept=0)
    for rep in reports:
        rep["dropped_empty"] = 0
    for rec in all_raw:
        rep = by_src[rec["source"]]
        if not any(rec.get(k) for k in ("title", "price", "description", "photos")):
            # Ficha sin ningún dato (p. ej. se recibió una página de reto en vez del anuncio)
            rep["dropped_empty"] += 1
            continue
        if not is_san_carlos(rec):
            rep["excluded_not_san_carlos"] += 1
            continue
        if rec.get("property_type") is not None and rec["property_type"] not in RESIDENTIAL_TYPES:
            rep["excluded_type"] += 1
            continue
        if FRACTIONAL.search(" ".join(filter(None, [rec.get("title"), rec.get("description")]))):
            # Venta fraccional / tiempo compartido: no es la compra de una casa
            rep["excluded_fractional"] = rep.get("excluded_fractional", 0) + 1
            continue
        rec.pop("_in_san_carlos_search", None)
        rep["kept"] += 1
        kept.append(rec)

    log: list[str] = []
    from .fx import fetch_usd_mxn
    fx = fetch_usd_mxn(log)

    geo_stats = None
    if not args.skip_geocode:
        from .geocode import geocode_all
        print("→ Geocodificando (Nominatim) ...", flush=True)
        geo_stats = geocode_all(kept, log)

    from .dedupe import dedupe
    print("→ Deduplicando ...", flush=True)
    if args.skip_photo_dedupe:
        import scraper.dedupe as d
        d.PhotoHasher.any_match = lambda self, a, b, n=4: None  # type: ignore[assignment]
    final, dd_stats = dedupe(kept, fx["usd_mxn"] if fx else None, log)

    # ---------- ids estables y first_seen_at
    prev_seen = {}
    prev_file = DATA / "listings.json"
    if prev_file.exists():
        try:
            for p in json.loads(prev_file.read_text(encoding="utf-8")).get("listings", []):
                for s in p.get("sources", []):
                    prev_seen[s["source_url"]] = p.get("first_seen_at")
        except (json.JSONDecodeError, KeyError):
            pass
    for rec in final:
        rec["id"] = hashlib.sha1(rec["source_url"].encode()).hexdigest()[:12]
        seen = [prev_seen.get(s["source_url"]) for s in rec["sources"] if prev_seen.get(s["source_url"])]
        rec["first_seen_at"] = min(seen) if seen else rec["scraped_at"]
        rec.pop("_notes", None)
        assert rec.get("source_url"), "propiedad sin source_url"

    final.sort(key=lambda r: (r.get("published_at") or r["first_seen_at"]), reverse=True)
    sources_used = sorted({s["source"] for r in final for s in r["sources"]})
    exact = sum(r.get("coords_precision") == "exacta" for r in final)
    approx = sum(r.get("coords_precision") == "aproximada" for r in final)

    out = {
        "generated_at": now_iso(),
        "run_started_at": run_started,
        "count": len(final),
        "sources": sources_used,
        "fx": fx,
        "listings": final,
    }
    (DATA / "listings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    report = {
        "run_started_at": run_started,
        "generated_at": out["generated_at"],
        "totals": {
            "raw_listings": len(all_raw),
            "kept_after_filters": len(kept),
            "final_after_dedupe": len(final),
            "coords_exacta": exact,
            "coords_aproximada": approx,
            "coords_null": len(final) - exact - approx,
            "pct_coords_exacta": round(100 * exact / len(final), 1) if final else 0,
        },
        "dedupe": dd_stats,
        "geocode": geo_stats,
        "fx": fx,
        "per_source": [{k: v for k, v in r.items() if k != "traceback"} for r in reports],
        "pipeline_log": log[-80:],
    }
    (DATA / "sources_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nResumen por fuente:")
    for r in reports:
        print(f"  {r['source']:<36} {r['status']:<22} crudos={r['raw_count']:<4} en San Carlos={r['kept']}")
    from .build_single import build
    print(f"Versión de un solo archivo: {build().name}")
    print(f"\nTotal final: {len(final)} propiedades de {len(sources_used)} fuentes "
          f"({report['totals']['pct_coords_exacta']}% con coordenadas exactas)")


if __name__ == "__main__":
    main()
