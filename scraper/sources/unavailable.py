"""Fuentes con inventario en San Carlos que NO se pueden extraer respetando sus
reglas. En cada corrida se vuelven a sondear (robots.txt + una sola petición a la
página de búsqueda de San Carlos con el User-Agent identificable) y el resultado
real queda en data/sources_report.json. Nunca se intenta evadir el bloqueo.
"""
from __future__ import annotations

import time
import urllib.robotparser

import requests

from ..common import BOT_NAME, USER_AGENT, looks_blocked, now_iso

PROBES = [
    {"source": "Inmuebles24", "site": "https://www.inmuebles24.com/",
     "url": "https://www.inmuebles24.com/casas-en-venta-en-san-carlos-nuevo-guaymas.html"},
    {"source": "Vivanuncios", "site": "https://www.vivanuncios.com.mx/",
     "url": "https://www.vivanuncios.com.mx/s-casas-en-venta/san-carlos-plaza_guaymas_sonora/v1c1293p1"},
    {"source": "Propiedades.com", "site": "https://propiedades.com/",
     "url": "https://propiedades.com/san-carlos-san-carlos-nuevo-guaymas--guaymas/casas-venta"},
    {"source": "MercadoLibre Inmuebles", "site": "https://inmuebles.mercadolibre.com.mx/",
     "url": "https://inmuebles.mercadolibre.com.mx/casas/venta/sonora/guaymas/",
     "robots_agents": ["Claude-User", "ClaudeBot"]},
    {"source": "Pincali (EasyBroker)", "site": "https://www.pincali.com/",
     "url": "https://www.pincali.com/inmuebles/casas-en-venta-en-guaymas-sonora"},
    {"source": "Properstar", "site": "https://www.properstar.com/",
     "url": "https://www.properstar.com/mexico/san-carlos-loc-1/buy"},
    {"source": "JamesEdition", "site": "https://www.jamesedition.com/",
     "url": "https://www.jamesedition.com/real_estate/san-carlos-mexico"},
]


def probe_all(log: list) -> list[dict]:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-MX,es;q=0.9"})
    out = []
    for p in PROBES:
        rep = {"key": None, "source": p["source"], "site": p["site"], "started_at": now_iso(),
               "raw_count": 0, "kept": 0, "errors": [], "notes": [], "probe_url": p["url"]}
        try:
            # 1) robots.txt para nuestro agente y para los agentes de Claude
            rr = s.get(p["site"].rstrip("/") + "/robots.txt", timeout=20)
            if rr.status_code in (401, 403):
                rep["status"] = "bloqueado"
                rep["errors"].append(f"robots.txt responde HTTP {rr.status_code} (acceso denegado al agente identificado); no se evade")
                out.append(rep)
                continue
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(rr.text.splitlines() if rr.status_code < 400 else [])
            disallowed_for = [a for a in [BOT_NAME] + p.get("robots_agents", []) if rr.status_code < 400 and not rp.can_fetch(a, p["url"])]
            if disallowed_for:
                rep["status"] = "robots_txt_no_permite"
                rep["errors"].append(f"robots.txt prohíbe {p['url']} para: {', '.join(disallowed_for)}")
                out.append(rep)
                continue
            # 2) dos peticiones espaciadas a la búsqueda de San Carlos: si alguna es
            #    denegada, el sitio está filtrando bots (aunque a veces deje pasar una)
            codes, blocked = [], False
            for attempt in range(2):
                if attempt:
                    time.sleep(4)
                r = s.get(p["url"], timeout=25)
                codes.append(r.status_code)
                if looks_blocked(r) or r.status_code in (202, 405, 429) or "san carlos" not in r.text.lower():
                    blocked = True
            if blocked:
                rep["status"] = "bloqueado"
                rep["errors"].append(f"HTTP {', '.join(map(str, codes))}: protección anti-bot / acceso denegado al agente identificado; no se evade")
            elif any(c >= 400 for c in codes):
                rep["status"] = "error"
                rep["errors"].append(f"HTTP {codes}")
            else:
                rep["status"] = "accesible_sin_scraper"
                rep["notes"].append(f"HTTP {codes}: respondió; falta implementar su scraper")
        except requests.RequestException as e:
            rep["status"] = "error"
            rep["errors"].append(f"Sin respuesta: {type(e).__name__}: {str(e)[:160]}")
        log.append(f"sondeo {p['source']}: {rep['status']}")
        out.append(rep)
    return out
