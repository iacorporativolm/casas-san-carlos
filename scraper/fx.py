"""Tipo de cambio USD/MXN actual desde fuentes públicas gratuitas.

1) Frankfurter (tipos de referencia del Banco Central Europeo)
2) open.er-api.com (ExchangeRate-API, plan abierto)
Si ambas fallan, se devuelve None y el sitio desactiva el conversor.
"""
from __future__ import annotations

from .common import Fetcher, now_iso


def fetch_usd_mxn(log: list) -> dict | None:
    f = Fetcher("FX", delay=1.0, respect_robots=False)
    try:
        d = f.get_json("https://api.frankfurter.app/latest", params={"from": "USD", "to": "MXN"})
        return {"usd_mxn": float(d["rates"]["MXN"]), "rate_date": d["date"],
                "provider": "Frankfurter (Banco Central Europeo)",
                "provider_url": "https://api.frankfurter.app/latest?from=USD&to=MXN",
                "fetched_at": now_iso()}
    except Exception as e:  # noqa: BLE001
        log.append(f"Frankfurter falló: {e}")
    try:
        d = f.get_json("https://open.er-api.com/v6/latest/USD")
        return {"usd_mxn": float(d["rates"]["MXN"]), "rate_date": d.get("time_last_update_utc"),
                "provider": "ExchangeRate-API (open.er-api.com)",
                "provider_url": "https://open.er-api.com/v6/latest/USD",
                "fetched_at": now_iso()}
    except Exception as e:  # noqa: BLE001
        log.append(f"open.er-api falló: {e}")
    return None
