# Casas y terrenos en San Carlos (Nuevo Guaymas, Sonora)

Buscador estilo Airbnb (lista + mapa) de casas, condominios y terrenos **en venta** en San Carlos,
construido **solo con anuncios reales** extraídos de portales y brokers. No hay datos de
ejemplo: si un anuncio no trae un dato, se guarda `null` y el sitio muestra "No especificado".

## Uso

```bash
pip install -r requirements.txt     # requests, beautifulsoup4, lxml, Pillow
npm install                         # Leaflet + markercluster (se copian a assets/vendor)
npm run refresh                     # = python3 -m scraper.refresh  (≈10 min)
npm run verify                      # verifica links y 10 anuncios al azar contra la fuente
npm run serve                       # http://localhost:8080
```

`python3 -m scraper.refresh --only next_br,remax` vuelve a extraer solo esas fuentes y reusa
la última corrida exitosa de las demás (`data/raw/<fuente>.json`).

## Datos

- `data/listings.json` — propiedades finales (deduplicadas). Cada una trae `source`,
  `source_url`, `listing_id`, `scraped_at` y `sources[]` (todos los portales donde aparece).
- `data/sources_report.json` — por fuente: estado, anuncios leídos, cuántos quedaron en
  San Carlos, errores; totales de coordenadas, deduplicación, geocodificación y tipo de cambio.
- `data/verify_report.json` — resultado de `npm run verify`.
- `data/geocode_cache.json` — caché de Nominatim.

### Reglas de calidad

| Tema | Regla |
|---|---|
| Campos faltantes | `null`, nunca se infieren. Si un anuncio no dice la moneda, `currency` = null y el sitio muestra "moneda no especificada". |
| Unidades | pies² y acres se convierten a m² (conversión, no estimación); el valor publicado queda en `extra`. |
| Coordenadas | `exacta` = vienen en el anuncio. Se descartan si caen fuera de San Carlos o si el portal repite el mismo punto en colonias distintas (valor por defecto). |
| Sin coordenadas | Se ubica la **colonia** → `aproximada` (zona punteada en el mapa): 1) Nominatim/OSM si el nombre del resultado coincide; 2) si OSM no tiene la colonia, mediana de las coordenadas exactas de ≥2 anuncios reales de esa misma colonia (todos a ≤1.5 km). El método queda en `geocode`. Si solo se sabe "San Carlos", no se ubica. |
| San Carlos | Se decide con datos del anuncio: coordenadas dentro del área (incluye el corredor del Blvd. hasta Villa Bemela; excluye Miramar/Las Tinajas y Guaymas), o título/zona/dirección, o la clasificación del propio portal. |
| Duplicados | Entre portales: mismo número MLS; o la clave de la inmobiliaria (p. ej. `SACV-6082`) aparece en el otro anuncio; o precio ±1.5 % + foto idéntica (dHash sobre recorte central, tolera fotos recortadas por el portal); o precio + m² construcción + m² terreno + recámaras + zona. En un mismo portal solo con misma dirección + precio + m² + fotos. La evidencia queda en `dedupe_evidence`. |
| Moneda | Se toma del anuncio. En el MLS, "MXN 3,000,000" = pesos y "$920,000" = moneda base que el portal declara en su listado (`setCurrencySymbol("USD")`); la base queda en `extra.moneda_fuente`. |
| Ventas fraccionales | "Venta fraccional / tiempo compartido" (un mes al año) no es la compra de una casa: se excluye y se cuenta en el reporte. |
| Tipo | Casa, Departamento/Condominio, Townhouse/Villa y **Terreno** (lotes sin construcción: residenciales, campestres y comerciales). Locales, oficinas, bodegas y ranchos se cuentan pero se excluyen. En terrenos el precio por m² usa la superficie del terreno y recámaras/baños se muestran como "No aplica". |

## Fuentes

| Fuente | Método | Notas |
|---|---|---|
| NEXT Bienes Raíces (nextbr.mx) | HTML (búsqueda ciudad San Carlos) | Coordenadas del anuncio |
| Pentágono Bienes Raíces (pentagonobr.com) | API REST pública de WordPress + ficha | Sin coordenadas → colonia geocodificada |
| RE/MAX México (remax.com.mx) | Endpoints JSON que usa su buscador | San Carlos está dentro de "Guaymas" |
| RE/MAX First Choice San Carlos | HTML + JSON-LD | Respeta `Crawl-Delay: 20` |
| Coldwell Banker San Carlos | API REST pública de WordPress (RealHomes) | Solo estatus "For Sale" |
| San Carlos MLS (Sonora MLS Regional) | HTML | Agrupa varias inmobiliarias locales; excluye ventas fraccionales |
| CENTURY 21 México | JSON del buscador (`?json=true`) | |
| Lamudi | HTML + JSON-LD | Oculta la ubicación en muchos anuncios; filtro anti-bot intermitente |

No utilizables (se sondean en cada corrida y se reportan): Inmuebles24, Vivanuncios,
Properstar, JamesEdition (bloqueo anti-bot 403), Pincali (verificación humana / HTTP 202),
MercadoLibre (su robots.txt prohíbe a los agentes de Claude), Propiedades.com (no responde
desde el entorno donde se corrió; probablemente filtra IPs de centros de datos).
No implementado: Coldwell Banker México (coldwellbanker.com.mx) — SPA de ~15 MB por página
cuyo HTML no trae los datos completos; su inventario de San Carlos parece ser el de la misma
oficina Coldwell Banker Encantado Realty que ya se extrae de su sitio propio.
No se intenta evadir ningún bloqueo.

Todas las peticiones usan el User-Agent `SanCarlosCasasBot/1.0`, revisan robots.txt y
esperan entre peticiones al mismo sitio (2.5 s por defecto, o el `Crawl-delay` del sitio).

## Frontend

Sitio estático (`index.html`, `assets/`), sin API keys: Leaflet + OpenStreetMap con pines de
precio, clustering, "Buscar en esta zona del mapa", filtros (precio, recámaras, baños, tipo, m²,
zona, fuente), MXN/USD con el tipo de cambio de la corrida, orden, detalle con todas las fotos y
links, favoritos (localStorage) y comparador de 2–3 propiedades.
