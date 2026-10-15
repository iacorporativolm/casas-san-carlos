"""Registro de fuentes: clave -> módulo en scraper/sources/.

Cada módulo define SOURCE (nombre visible), SITE (URL), opcional DELAY, y
scrape(fetcher) -> (lista de make_listing, notas).
Las fuentes que no se pueden usar (bloqueo anti-bot o robots.txt) están en
scraper/sources/unavailable.py y se reportan en data/sources_report.json.
"""
SOURCES = {
    "next_br": "next_br",                       # obligatoria: NEXT Bienes Raíces
    "pentagono": "pentagono",                   # obligatoria: Pentágono Bienes Raíces
    "remax": "remax",                           # obligatoria: RE/MAX México
    "remax_firstchoice": "remax_firstchoice",   # RE/MAX First Choice (broker local)
    "coldwell_sancarlos": "coldwell_sancarlos", # Coldwell Banker Encantado Realty (local)
    "sancarlos_mls": "sancarlos_mls",           # MLS AMPI Guaymas-San Carlos
    "century21": "century21",                   # CENTURY 21 México
    "lamudi": "lamudi",                         # Lamudi (portal)
}
