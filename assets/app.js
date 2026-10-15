/* Casas en San Carlos — frontend estático (sin API keys).
 * Lee data/listings.json y data/sources_report.json generados por `npm run refresh`.
 * Ningún dato se inventa aquí: lo que falta en el anuncio se muestra como "No especificado". */
(() => {
  'use strict';

  // ------------------------------------------------------------- utilidades
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : null);
  const NS = '<span class="ns">No especificado</span>';
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
  };
  const fmtNum = (n, d = 0) => Number(n).toLocaleString('es-MX', { maximumFractionDigits: d });
  const fmtDate = (iso, withTime = true) => {
    if (!iso) return null;
    // "2026-09-28" (solo fecha) se interpreta como fecha local, no como medianoche UTC
    const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const d = dm ? new Date(+dm[1], +dm[2] - 1, +dm[3]) : new Date(iso);
    if (Number.isNaN(d.getTime())) return esc(iso);
    return d.toLocaleString('es-MX', withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' });
  };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ----------------------------------------------------------------- estado
  const state = {
    all: [],
    byId: new Map(),
    fx: null,
    meta: null,
    report: null,
    ccy: LS.get('sc_ccy', 'MXN'),
    sort: LS.get('sc_sort', 'recent'),
    f: { priceMin: null, priceMax: null, beds: 0, baths: 0, types: new Set(), m2Min: null, m2Max: null, landMin: null, zones: new Set(), sources: new Set() },
    favsOnly: false,
    bounds: null,
    favs: new Set(LS.get('sc_favs', [])),
    compare: LS.get('sc_compare', []),
    filtered: [],
  };

  // ---------------------------------------------------------------- precios
  function priceIn(r, ccy) {
    if (r.price == null || !r.currency) return null;
    if (!state.fx || r.currency === ccy) return r.currency === ccy ? r.price : null;
    return r.currency === 'USD' ? r.price * state.fx.usd_mxn : r.price / state.fx.usd_mxn;
  }
  // Moneda de despliegue efectiva: si no hay tipo de cambio, cada precio va en su moneda original
  const displayCcy = (r) => (state.fx ? state.ccy : r.currency);
  const money = (v, ccy) => `${ccy === 'USD' ? 'US$' : '$'}${fmtNum(Math.round(v))} ${ccy}`;
  function short(v) {
    if (v >= 1e6) { const m = v / 1e6; return `$${m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, '')}M`; }
    if (v >= 1e3) return `$${Math.round(v / 1e3)}K`;
    return `$${Math.round(v)}`;
  }
  function priceLabel(r) {
    if (r.price == null) return { main: 'Precio no especificado', sub: '', pin: 'N/E', value: null };
    if (!r.currency) return { main: `$${fmtNum(r.price)}`, sub: 'moneda no especificada', pin: short(r.price), value: null };
    const ccy = displayCcy(r);
    const v = priceIn(r, ccy);
    const converted = ccy !== r.currency;
    return {
      main: `${converted ? '≈ ' : ''}${money(v, ccy)}`,
      sub: converted ? `publicado en ${money(r.price, r.currency)}` : '',
      pin: short(v),
      value: v,
    };
  }
  const ppm2 = (r) => {
    const v = priceIn(r, displayCcy(r) || 'MXN');
    return v != null && r.construction_m2 ? v / r.construction_m2 : null;
  };
  const sourcesOf = (r) => (r.sources && r.sources.length ? r.sources : [{ source: r.source, source_url: r.source_url, listing_id: r.listing_id, price: r.price, currency: r.currency }]);

  // ---------------------------------------------------------------- filtros
  function filterChecks(r) {
    // Devuelve [pasa, ocultoPorDatoFaltante]
    const f = state.f;
    let missing = false;
    const need = (cond, value) => {
      if (!cond) return true;
      if (value == null) { missing = true; return false; }
      return null; // continuar con la comparación
    };
    if (f.priceMin != null || f.priceMax != null) {
      const v = priceIn(r, state.fx ? state.ccy : r.currency);
      if (need(true, v) === false) return [false, missing];
      if (f.priceMin != null && v < f.priceMin) return [false, false];
      if (f.priceMax != null && v > f.priceMax) return [false, false];
    }
    if (f.beds) { if (need(true, r.bedrooms) === false) return [false, missing]; if (r.bedrooms < f.beds) return [false, false]; }
    if (f.baths) { if (need(true, r.bathrooms) === false) return [false, missing]; if (r.bathrooms < f.baths) return [false, false]; }
    if (f.types.size) { if (need(true, r.property_type) === false) return [false, missing]; if (!f.types.has(r.property_type)) return [false, false]; }
    if (f.m2Min != null || f.m2Max != null) {
      if (need(true, r.construction_m2) === false) return [false, missing];
      if (f.m2Min != null && r.construction_m2 < f.m2Min) return [false, false];
      if (f.m2Max != null && r.construction_m2 > f.m2Max) return [false, false];
    }
    if (f.landMin != null) { if (need(true, r.land_m2) === false) return [false, missing]; if (r.land_m2 < f.landMin) return [false, false]; }
    if (f.zones.size) { if (need(true, r.zone) === false) return [false, missing]; if (!f.zones.has(r.zone)) return [false, false]; }
    if (f.sources.size && !sourcesOf(r).some((s) => f.sources.has(s.source))) return [false, false];
    if (state.favsOnly && !state.favs.has(r.id)) return [false, false];
    if (state.bounds) {
      if (r.lat == null) { missing = true; return [false, missing]; }
      if (!state.bounds.contains([r.lat, r.lng])) return [false, false];
    }
    return [true, false];
  }

  function sortList(list) {
    const s = state.sort;
    const nullsLast = (a, b, fa, fb, dir) => {
      if (fa == null && fb == null) return 0;
      if (fa == null) return 1;
      if (fb == null) return -1;
      return dir * (fa - fb);
    };
    // "Más reciente": fecha de publicación del anuncio; los que no la publican van al final
    // (ordenados por la fecha en que este sitio los vio por primera vez).
    const pub = (r) => Date.parse(r.published_at) || null;
    const seen = (r) => Date.parse(r.first_seen_at || r.scraped_at) || 0;
    const ccyOf = (r) => (state.fx ? state.ccy : r.currency);
    return list.sort((a, b) => {
      if (s === 'price_asc') return nullsLast(a, b, priceIn(a, ccyOf(a)), priceIn(b, ccyOf(b)), 1);
      if (s === 'price_desc') return nullsLast(a, b, priceIn(a, ccyOf(a)), priceIn(b, ccyOf(b)), -1);
      if (s === 'ppm2_asc') return nullsLast(a, b, ppm2(a), ppm2(b), 1);
      return nullsLast(a, b, pub(a), pub(b), -1) || seen(b) - seen(a);
    });
  }

  function applyFilters() {
    let hidden = 0;
    const out = [];
    for (const r of state.all) {
      const [ok, miss] = filterChecks(r);
      if (ok) out.push(r); else if (miss) hidden += 1;
    }
    state.filtered = sortList(out);
    renderList(hidden);
    renderMarkers();
    renderPills();
  }

  // ------------------------------------------------------------------- mapa
  const SAN_CARLOS = [27.958, -111.045];
  let map, cluster, zoneLayer;
  const markers = new Map();
  let programmaticMove = false;
  let hotEl = null;

  function initMap() {
    map = L.map('map', { zoomControl: true, attributionControl: true }).setView(SAN_CARLOS, 13);
    window.sanCarlosMap = map; // para pruebas y depuración
    // Capas base sin API key. tile.openstreetmap.org (y CARTO) rechazan las peticiones
    // sin "Referer", que es lo que pasa al abrir el sitio como archivo local (file://).
    // Por eso: servido por http(s) → OpenStreetMap oficial; archivo local → Esri World Street Map.
    const isFile = location.protocol === 'file:';
    const streets = isFile
      ? L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19, className: 'tiles-streets',
        attribution: 'Mapa &copy; Esri, HERE, Garmin, colaboradores de <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      })
      : L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, className: 'tiles-streets',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      });
    const satellite = L.layerGroup([
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19, attribution: 'Imágenes &copy; Esri, Maxar, Earthstar Geographics',
      }),
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19, className: 'tiles-labels',
      }),
    ]);
    const bases = { Mapa: streets, 'Satélite': satellite };
    const saved = LS.get('sc_basemap', 'Mapa');
    (bases[saved] || streets).addTo(map);
    L.control.layers(bases, null, { position: 'topright', collapsed: false }).addTo(map);
    map.on('baselayerchange', (e) => LS.set('sc_basemap', e.name));
    zoneLayer = L.layerGroup().addTo(map);
    cluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 44,
      disableClusteringAtZoom: 16,
      spiderfyOnMaxZoom: true,
      iconCreateFunction: (c) => L.divIcon({ html: `<span>${c.getChildCount()}</span>`, className: 'cluster-pin', iconSize: [40, 40] }),
    });
    map.addLayer(cluster);
    const onUserMove = () => {
      if (programmaticMove) { programmaticMove = false; return; }
      $('#search-area').hidden = false;
    };
    map.on('dragend', onUserMove);
    map.on('zoomend', () => { if (!programmaticMove) $('#search-area').hidden = false; });
    map.on('moveend', () => { programmaticMove = false; });
    $('#search-area').addEventListener('click', () => {
      state.bounds = map.getBounds();
      $('#search-area').hidden = true;
      applyFilters();
    });
  }

  function popupHtml(r) {
    const p = priceLabel(r);
    const img = safeUrl((r.photos || [])[0]);
    const approx = r.coords_precision === 'aproximada';
    return `<div class="map-popup" data-open="${esc(r.id)}">
      ${img ? `<img src="${esc(img)}" alt="" referrerpolicy="no-referrer" loading="lazy">` : ''}
      <b>${esc(p.main)}</b>
      <div class="muted">${esc(specsText(r))}</div>
      <div class="muted">${esc(r.zone || 'Zona no especificada')} · ${esc([...new Set(sourcesOf(r).map((s) => s.source))].join(', '))}</div>
      ${approx ? '<div class="warn">≈ Ubicación aproximada (centro de la colonia/zona, geocodificada)</div>' : ''}
    </div>`;
  }

  function renderMarkers() {
    cluster.clearLayers();
    zoneLayer.clearLayers();
    markers.clear();
    const zones = new Map();
    const layers = [];
    for (const r of state.filtered) {
      if (r.lat == null || r.lng == null) continue;
      const approx = r.coords_precision === 'aproximada';
      const p = priceLabel(r);
      const icon = L.divIcon({
        className: `price-pin${approx ? ' approx' : ''}${state.favs.has(r.id) ? ' fav' : ''}`,
        html: `<span>${approx ? '≈ ' : ''}${esc(p.pin)}</span>`,
        iconSize: null,
      });
      const m = L.marker([r.lat, r.lng], { icon, title: `${p.main}${approx ? ' (ubicación aproximada)' : ''}`, riseOnHover: true });
      m.bindPopup(popupHtml(r), { closeButton: false, offset: [0, -12] });
      m.on('mouseover', () => cardHot(r.id, true));
      m.on('mouseout', () => cardHot(r.id, false));
      m.on('popupopen', (e) => {
        const el = e.popup.getElement().querySelector('[data-open]');
        if (el) el.addEventListener('click', () => openDetail(r.id));
      });
      markers.set(r.id, m);
      layers.push(m);
      if (approx) {
        const key = `${r.lat},${r.lng}`;
        if (!zones.has(key)) zones.set(key, { r, n: 0 });
        zones.get(key).n += 1;
      }
    }
    cluster.addLayers(layers);
    for (const { r, n } of zones.values()) {
      L.circle([r.lat, r.lng], {
        radius: Math.min(r.approx_radius_m || 400, 700), // tope visual; el radio real va en el tooltip
        color: getComputedStyle(document.documentElement).getPropertyValue('--approx').trim() || '#8a5a00',
        weight: 1.2, dashArray: '4 6', fillOpacity: 0.05, interactive: true,
      }).bindTooltip(`Zona aproximada: ${esc(r.zone || '')} — ${n} propiedad${n > 1 ? 'es' : ''} sin coordenadas en su anuncio.<br>Radio aprox. ${Math.round(r.approx_radius_m || 400)} m. ${esc(r.geocode?.service || '')}`, { sticky: true })
        .addTo(zoneLayer);
    }
  }

  function fitAll() {
    const pts = state.all.filter((r) => r.lat != null).map((r) => [r.lat, r.lng]);
    programmaticMove = true;
    if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
    else map.setView(SAN_CARLOS, 13);
  }

  function markerHot(id, on) {
    if (hotEl) { hotEl.classList.remove('is-hot'); hotEl = null; }
    if (!on) return;
    const m = markers.get(id);
    if (!m) return;
    const vis = cluster.getVisibleParent(m);
    const el = vis && vis.getElement ? vis.getElement() : null;
    if (el) { el.classList.add('is-hot'); hotEl = el; }
  }
  function cardHot(id, on) {
    const el = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
    if (el) el.classList.toggle('is-hot', on);
  }

  // ---------------------------------------------------------------- tarjetas
  function specsText(r) {
    const parts = [];
    parts.push(r.bedrooms != null ? `${fmtNum(r.bedrooms, 1)} rec.` : 'rec. N/E');
    parts.push(r.bathrooms != null ? `${fmtNum(r.bathrooms, 1)} baños` : 'baños N/E');
    parts.push(r.construction_m2 != null ? `${fmtNum(r.construction_m2)} m² const.` : 'm² N/E');
    if (r.land_m2 != null) parts.push(`${fmtNum(r.land_m2)} m² terreno`);
    return parts.join(' · ');
  }
  const heart = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 28c7-4.7 14-10 14-17a6.9 6.9 0 0 0-14-3.3A6.9 6.9 0 0 0 2 11c0 7 7 12.3 14 17z"/></svg>';

  function cardHtml(r) {
    const photos = (r.photos || []).map(safeUrl).filter(Boolean);
    const p = priceLabel(r);
    const pm = ppm2(r);
    const srcs = sourcesOf(r);
    const imgs = photos.map((u, i) => `<img ${i === 0 ? `src="${esc(u)}"` : `data-src="${esc(u)}"`} alt="Foto ${i + 1} de ${esc(r.title || 'la propiedad')}" referrerpolicy="no-referrer" loading="lazy" decoding="async">`).join('');
    const dots = photos.length > 1 ? `<div class="dots">${photos.slice(0, 5).map((_, i) => `<i class="${i === 0 ? 'on' : ''}"></i>`).join('')}</div>` : '';
    const inCmp = state.compare.includes(r.id);
    return `<article class="card" data-id="${esc(r.id)}" tabindex="0" aria-label="${esc(p.main)} — ${esc(r.zone || '')}">
      <div class="photo" data-i="0" data-n="${photos.length}">
        ${photos.length ? `<div class="track">${imgs}</div>` : '<div class="nophoto">El anuncio no incluye fotos</div>'}
        ${photos.length > 1 ? '<button class="nav prev" type="button" aria-label="Foto anterior">‹</button><button class="nav next" type="button" aria-label="Foto siguiente">›</button>' : ''}
        ${dots}
        <button class="fav ${state.favs.has(r.id) ? 'on' : ''}" type="button" aria-label="Guardar en favoritos" aria-pressed="${state.favs.has(r.id)}">${heart}</button>
        <label class="cmp" title="Comparar"><input type="checkbox" ${inCmp ? 'checked' : ''} aria-label="Comparar"> Comparar</label>
        <div class="badges">${[...new Set(srcs.map((s) => s.source))].map((n) => `<span class="badge">${esc(n)}</span>`).join('')}</div>
      </div>
      <div class="card-body">
        <div class="line1"><span class="zone">${esc(r.zone || 'Zona no especificada')}</span><span class="type">${esc(r.property_type || '')}</span></div>
        <div class="title">${esc(r.title || '')}</div>
        <div class="specs">${esc(specsText(r))}</div>
        ${r.published_at ? `<div class="title">Publicado ${esc(fmtDate(r.published_at, false))}</div>` : ''}
        <div class="price"><b>${esc(p.main)}</b>${p.sub ? ` <small>· ${esc(p.sub)}</small>` : ''}${pm ? ` <small>· ${short(pm).replace('M', 'M')}/m²</small>` : ''}</div>
        <div class="orig">
          <a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Ver anuncio original ↗</a>
          ${r.coords_precision === 'aproximada' ? '<span class="approx-tag">≈ ubicación aproximada</span>' : ''}
          ${r.lat == null ? '<span class="approx-tag">sin ubicación en mapa</span>' : ''}
        </div>
      </div>
    </article>`;
  }

  function renderList(hiddenByMissing) {
    const grid = $('#grid');
    const n = state.filtered.length;
    $('#result-count').textContent = state.all.length
      ? `${fmtNum(n)} ${n === 1 ? 'propiedad' : 'propiedades'}${n !== state.all.length ? ` de ${fmtNum(state.all.length)}` : ''}`
      : '';
    const note = $('#hidden-note');
    note.hidden = !hiddenByMissing;
    note.textContent = hiddenByMissing
      ? `${hiddenByMissing} propiedad${hiddenByMissing > 1 ? 'es ocultas porque su anuncio no especifica' : ' oculta porque su anuncio no especifica'} el dato filtrado (o no tiene ubicación, si filtras por zona del mapa).`
      : '';
    grid.innerHTML = state.filtered.map(cardHtml).join('');
    const empty = $('#empty');
    if (!state.all.length) {
      empty.hidden = false;
      empty.innerHTML = emptyStateHtml();
    } else if (!n) {
      empty.hidden = false;
      empty.innerHTML = '<h2>Sin resultados</h2><p>Ninguna propiedad cumple con los filtros. Prueba quitando alguno.</p>';
    } else {
      empty.hidden = true;
    }
    renderChips();
  }

  function emptyStateHtml() {
    const rep = state.report;
    const rows = rep && rep.per_source ? rep.per_source.map((s) => `<tr><td>${esc(s.source)}</td><td class="${s.status === 'ok' ? 'status-ok' : 'status-bad'}">${esc(STATUS_TXT[s.status] || s.status)}</td><td>${esc((s.errors || [])[0] || '')}</td></tr>`).join('') : '';
    return `<h2>Todavía no hay propiedades</h2>
      <p>Este sitio solo muestra anuncios reales extraídos de los portales. No hay datos de ejemplo.</p>
      <p>Para cargar datos corre <code>npm run refresh</code> y recarga la página.</p>
      ${rows ? `<div class="table-scroll"><table class="src-table"><thead><tr><th>Fuente</th><th>Estado</th><th>Detalle</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}`;
  }

  function wireGrid() {
    const grid = $('#grid');
    grid.addEventListener('click', (e) => {
      const card = e.target.closest('.card');
      if (!card) return;
      const id = card.dataset.id;
      if (e.target.closest('.nav')) { e.stopPropagation(); slide(card.querySelector('.photo'), e.target.closest('.nav').classList.contains('next') ? 1 : -1); return; }
      if (e.target.closest('.fav')) { toggleFav(id); return; }
      if (e.target.closest('.cmp')) { if (e.target.tagName === 'INPUT') toggleCompare(id, e.target); return; }
      if (e.target.closest('a')) return;
      openDetail(id);
    });
    grid.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.classList.contains('card')) openDetail(e.target.dataset.id);
    });
    grid.addEventListener('mouseover', (e) => {
      const card = e.target.closest('.card');
      if (card && !card.contains(e.relatedTarget)) markerHot(card.dataset.id, true);
    });
    grid.addEventListener('mouseout', (e) => {
      const card = e.target.closest('.card');
      if (card && !card.contains(e.relatedTarget)) markerHot(card.dataset.id, false);
    });
    grid.addEventListener('error', (e) => {
      if (e.target.tagName === 'IMG') {
        const ph = document.createElement('div');
        ph.className = 'nophoto';
        ph.style.flex = '0 0 100%';
        ph.textContent = 'Foto no disponible (el portal no permite mostrarla)';
        e.target.replaceWith(ph);
      }
    }, true);
  }

  function slide(photo, dir) {
    const n = Number(photo.dataset.n);
    let i = (Number(photo.dataset.i) + dir + n) % n;
    photo.dataset.i = i;
    const imgs = photo.querySelectorAll('.track > *');
    [i, (i + 1) % n].forEach((k) => { const im = imgs[k]; if (im && im.dataset && im.dataset.src) { im.src = im.dataset.src; delete im.dataset.src; } });
    photo.querySelector('.track').style.transform = `translateX(-${i * 100}%)`;
    const dots = photo.querySelectorAll('.dots i');
    if (dots.length) {
      // Con más de 5 fotos, los 5 puntos representan la posición proporcional
      const on = n <= dots.length ? i : Math.round((i * (dots.length - 1)) / (n - 1));
      dots.forEach((el, k) => el.classList.toggle('on', k === on));
    }
  }

  // --------------------------------------------------- favoritos y comparar
  function toggleFav(id) {
    state.favs.has(id) ? state.favs.delete(id) : state.favs.add(id);
    LS.set('sc_favs', [...state.favs]);
    $$(`.card[data-id="${CSS.escape(id)}"] .fav`).forEach((b) => { b.classList.toggle('on', state.favs.has(id)); b.setAttribute('aria-pressed', state.favs.has(id)); });
    const db = $(`#modal [data-fav="${CSS.escape(id)}"]`);
    if (db) db.textContent = state.favs.has(id) ? '♥ Guardada' : '♡ Guardar';
    if (state.favsOnly) applyFilters(); else renderMarkers();
  }

  function toggleCompare(id, input) {
    const i = state.compare.indexOf(id);
    if (i >= 0) state.compare.splice(i, 1);
    else if (state.compare.length >= 3) { if (input) input.checked = false; toast('Puedes comparar máximo 3 propiedades'); return; }
    else state.compare.push(id);
    LS.set('sc_compare', state.compare);
    $$('.card').forEach((c) => { const cb = c.querySelector('.cmp input'); if (cb) cb.checked = state.compare.includes(c.dataset.id); });
    renderCompareBar();
  }

  function renderCompareBar() {
    state.compare = state.compare.filter((id) => state.byId.has(id));
    const bar = $('#compare-bar');
    bar.hidden = !state.compare.length;
    $('#compare-items').innerHTML = state.compare.map((id) => {
      const r = state.byId.get(id);
      const img = safeUrl((r.photos || [])[0]);
      return `<span class="compare-item">${img ? `<img src="${esc(img)}" alt="" referrerpolicy="no-referrer">` : '<span class="ph"></span>'}${esc(priceLabel(r).pin)} · ${esc((r.zone || '').slice(0, 18))}<button type="button" data-rm="${esc(id)}" aria-label="Quitar">×</button></span>`;
    }).join('');
    $('#btn-compare').disabled = state.compare.length < 2;
    $('#btn-compare').textContent = state.compare.length < 2 ? 'Elige 2–3 para comparar' : `Comparar (${state.compare.length})`;
  }

  function openCompare() {
    const rs = state.compare.map((id) => state.byId.get(id)).filter(Boolean);
    const vals = (fn) => rs.map(fn);
    const best = (arr, dir) => { const nums = arr.filter((v) => v != null); if (nums.length < 2) return null; return dir < 0 ? Math.min(...nums) : Math.max(...nums); };
    const row = (label, fn, fmt, dir) => {
      const raw = vals(fn);
      const b = dir ? best(raw, dir) : null;
      return `<tr><th>${label}</th>${raw.map((v, i) => `<td class="${b != null && v === b ? 'best' : ''}">${v == null ? NS : fmt(v, rs[i])}</td>`).join('')}</tr>`;
    };
    const approx = (r) => (displayCcy(r) !== r.currency ? '≈ ' : '');
    const cur = (r) => displayCcy(r);
    const html = `
      <div class="modal-head"><div><h2>Comparar propiedades</h2><div class="sub">Datos tal como vienen en cada anuncio. En verde, el mejor valor de cada fila.</div></div><button class="close" type="button" data-close aria-label="Cerrar">✕</button></div>
      <div class="table-scroll"><table class="cmp-table">
        <tr><th></th>${rs.map((r) => `<td>${safeUrl((r.photos || [])[0]) ? `<img src="${esc(r.photos[0])}" alt="" referrerpolicy="no-referrer">` : NS}<div><b>${esc(r.zone || 'Zona no especificada')}</b></div></td>`).join('')}</tr>
        ${row('Precio', (r) => priceIn(r, cur(r)), (v, r) => esc(approx(r) + money(v, cur(r))), -1)}
        ${row('Precio por m²', (r) => ppm2(r), (v, r) => esc(approx(r) + money(v, cur(r))), -1)}
        ${row('Tipo', (r) => r.property_type, esc)}
        ${row('Recámaras', (r) => r.bedrooms, (v) => fmtNum(v, 1), 1)}
        ${row('Baños', (r) => r.bathrooms, (v) => fmtNum(v, 1), 1)}
        ${row('m² construcción', (r) => r.construction_m2, (v) => `${fmtNum(v)} m²`, 1)}
        ${row('m² terreno', (r) => r.land_m2, (v) => `${fmtNum(v)} m²`, 1)}
        ${row('Ubicación', (r) => r.coords_precision, (v) => (v === 'exacta' ? 'Exacta (del anuncio)' : 'Aproximada (colonia)'))}
        <tr><th>Fuentes</th>${rs.map((r) => `<td>${sourcesOf(r).map((s) => `<a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">${esc(s.source)} ↗</a>`).join('<br>')}</td>`).join('')}</tr>
        <tr><th></th>${rs.map((r) => `<td><button class="btn-text" type="button" data-detail="${esc(r.id)}">Ver detalle</button></td>`).join('')}</tr>
      </table></div>`;
    openModal(html);
  }

  // ------------------------------------------------------------------ detalle
  function factsHtml(r) {
    const fs = r.field_sources || {};
    const from = (k) => (fs[k] ? ` <span class="small">(de ${esc(fs[k])})</span>` : '');
    const v = (val, fmt = esc) => (val == null || val === '' ? NS : fmt(val));
    const pm = ppm2(r);
    const items = [
      ['Tipo', v(r.property_type) + from('property_type')],
      ['Recámaras', v(r.bedrooms, (x) => fmtNum(x, 1)) + from('bedrooms')],
      ['Baños', v(r.bathrooms, (x) => fmtNum(x, 1)) + from('bathrooms')],
      ['m² construcción', v(r.construction_m2, (x) => `${fmtNum(x)} m²`) + from('construction_m2')],
      ['m² terreno', v(r.land_m2, (x) => `${fmtNum(x)} m²`) + from('land_m2')],
      ['Precio por m²', pm ? esc(money(pm, displayCcy(r))) : NS],
      ['Colonia / zona', v(r.zone) + from('zone')],
      ['Dirección', v(r.address) + from('address')],
      ['Publicado', r.published_at ? esc(fmtDate(r.published_at, false)) : NS],
      ['Visto por primera vez', esc(fmtDate(r.first_seen_at))],
      ['Datos extraídos', esc(fmtDate(r.scraped_at))],
      ['ID del anuncio', v(r.listing_id)],
    ];
    const ex = r.extra || {};
    const conv = (orig, m2) => orig && m2 != null && !/m(2|²|t|eter)|metro/i.test(orig);
    if (conv(ex.area_publicada, r.construction_m2)) items.push(['Construcción publicada', `${esc(ex.area_publicada)} <span class="small">(convertida a m²)</span>`]);
    if (conv(ex.terreno_publicado, r.land_m2)) items.push(['Terreno publicado', `${esc(ex.terreno_publicado)} <span class="small">(convertido a m²)</span>`]);
    if (ex.medios_banos) items.push(['Medios baños', esc(fmtNum(ex.medios_banos))]);
    if (ex.mls_san_carlos || ex.mls) items.push(['Número MLS', esc(ex.mls_san_carlos || ex.mls)]);
    if (fs.currency) items.push(['Moneda', `${esc(r.currency)} <span class="small">(indicada en ${esc(fs.currency)}; la otra fuente no la especifica)</span>`]);
    return `<dl class="facts">${items.map(([k, val]) => `<div><dt>${k}</dt><dd>${val}</dd></div>`).join('')}</dl>`;
  }

  function contactHtml(c) {
    if (!c || typeof c !== 'object') return NS;
    const parts = [];
    if (c.agency) parts.push(`<div><b>${esc(c.agency)}</b></div>`);
    if (c.agent) parts.push(`<div>${esc(c.agent)}</div>`);
    if (c.phone) parts.push(`<div><a href="tel:${esc(String(c.phone).replace(/[^\d+]/g, ''))}">${esc(c.phone)}</a></div>`);
    if (c.email) parts.push(`<div><a href="mailto:${esc(c.email)}">${esc(c.email)}</a></div>`);
    return parts.length ? parts.join('') : NS;
  }

  function portalsLine(r) {
    const n = sourcesOf(r).length;
    const p = new Set(sourcesOf(r).map((s) => s.source)).size;
    if (n === 1) return 'Publicado en 1 portal';
    return n === p ? `Publicado en ${p} portales` : `${n} anuncios en ${p} portal${p > 1 ? 'es' : ''}`;
  }

  function openDetail(id) {
    const r = state.byId.get(id);
    if (!r) return;
    const photos = (r.photos || []).map(safeUrl).filter(Boolean);
    const p = priceLabel(r);
    const gal = photos.length
      ? `<div class="gallery">${photos.slice(0, 5).map((u, i) => `<img src="${esc(u)}" alt="Foto ${i + 1}" data-lb="${i}" referrerpolicy="no-referrer" loading="lazy">`).join('')}</div>
         ${photos.length > 5 ? `<p><button class="btn-text" type="button" data-lb="0">Ver las ${photos.length} fotos</button></p>` : ''}`
      : '<div class="gallery-empty">El anuncio no incluye fotos</div>';
    const precision = r.coords_precision === 'exacta'
      ? '<p class="small">Ubicación: coordenadas publicadas en el anuncio.</p>'
      : r.coords_precision === 'aproximada'
        ? `<div class="warn-box">≈ Ubicación aproximada. El anuncio no trae coordenadas, así que se muestra la zona de la colonia “${esc(r.zone || r.address || '')}”. Método: ${esc(r.geocode?.service || 'OpenStreetMap')}. No es la ubicación exacta de la propiedad.</div>`
        : '<div class="warn-box">El anuncio no trae ubicación suficiente para ponerla en el mapa.</div>';
    const html = `
      <div class="modal-head">
        <div><h2>${esc(r.title || 'Propiedad en venta')}</h2><div class="sub">${esc(r.zone || 'Zona no especificada')} · San Carlos, Sonora</div></div>
        <button class="close" type="button" data-close aria-label="Cerrar">✕</button>
      </div>
      ${gal}
      <div class="detail-grid">
        <div>
          ${factsHtml(r)}
          <h3>Descripción</h3>
          <div class="desc">${r.description ? esc(r.description) : NS}</div>
          <h3>Contacto / inmobiliaria</h3>
          <div>${contactHtml(r.contact)}</div>
        </div>
        <aside>
          <div class="side-card">
            <div class="big">${esc(p.main)}</div>
            ${p.sub ? `<div class="small">${esc(p.sub)}</div>` : ''}
            <p style="display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 0">
              <a class="btn-primary" style="text-decoration:none" href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Ver anuncio original ↗</a>
              <button class="pill" type="button" data-fav="${esc(r.id)}">${state.favs.has(r.id) ? '♥ Guardada' : '♡ Guardar'}</button>
              <button class="pill" type="button" data-cmp="${esc(r.id)}">${state.compare.includes(r.id) ? '✓ En comparación' : '+ Comparar'}</button>
            </p>
            <h4>${portalsLine(r)}</h4>
            <ul class="src-list">${sourcesOf(r).map((s) => `<li><a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">${esc(s.source)} ↗</a>
              <div class="small">${s.price != null && s.currency ? esc(money(s.price, s.currency)) : 'Precio no especificado'}${s.listing_id ? ` · ID ${esc(s.listing_id)}` : ''}${s.scraped_at ? ` · extraído ${esc(fmtDate(s.scraped_at))}` : ''}</div></li>`).join('')}</ul>
            ${r.dedupe_evidence && r.dedupe_evidence.length ? `<p class="small">Unificado por: ${esc(r.dedupe_evidence.join('; '))}</p>` : ''}
            ${precision}
            ${r.lat != null ? `<p><button class="btn-text" type="button" data-flyto="${esc(r.id)}">Ver en el mapa</button></p>` : ''}
          </div>
        </aside>
      </div>`;
    openModal(html);
    const body = $('#modal-body');
    body.querySelectorAll('[data-lb]').forEach((el) => el.addEventListener('click', () => lightbox(photos, Number(el.dataset.lb))));
    body.querySelectorAll('img').forEach((im) => im.addEventListener('error', () => { im.style.visibility = 'hidden'; }));
  }

  function lightbox(photos, start) {
    let i = start;
    const box = document.createElement('div');
    box.className = 'lightbox';
    box.innerHTML = '<img alt="" referrerpolicy="no-referrer"><button class="lb-close" aria-label="Cerrar">✕</button><button class="lb-prev" aria-label="Anterior">‹</button><button class="lb-next" aria-label="Siguiente">›</button><span class="lb-count"></span>';
    const show = () => { box.querySelector('img').src = photos[i]; box.querySelector('.lb-count').textContent = `${i + 1} / ${photos.length}`; };
    const close = () => { box.remove(); document.removeEventListener('keydown', key, true); };
    const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); } if (e.key === 'ArrowRight') { i = (i + 1) % photos.length; show(); } if (e.key === 'ArrowLeft') { i = (i - 1 + photos.length) % photos.length; show(); } };
    box.addEventListener('click', (e) => {
      if (e.target.classList.contains('lb-next')) { i = (i + 1) % photos.length; show(); } else if (e.target.classList.contains('lb-prev')) { i = (i - 1 + photos.length) % photos.length; show(); } else if (e.target.tagName !== 'IMG') close();
    });
    document.addEventListener('keydown', key, true);
    $('#modal').appendChild(box);
    show();
  }

  function openModal(html) {
    const dlg = $('#modal');
    $('#modal-body').innerHTML = html;
    if (!dlg.open) dlg.showModal();
    dlg.scrollTop = 0;
  }

  function wireModal() {
    const dlg = $('#modal');
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg || e.target.closest('[data-close]')) { dlg.close(); return; }
      const fav = e.target.closest('[data-fav]');
      if (fav) { toggleFav(fav.dataset.fav); return; }
      const cmp = e.target.closest('[data-cmp]');
      if (cmp) { toggleCompare(cmp.dataset.cmp); cmp.textContent = state.compare.includes(cmp.dataset.cmp) ? '✓ En comparación' : '+ Comparar'; return; }
      const det = e.target.closest('[data-detail]');
      if (det) { openDetail(det.dataset.detail); return; }
      const fly = e.target.closest('[data-flyto]');
      if (fly) {
        const r = state.byId.get(fly.dataset.flyto);
        dlg.close();
        document.body.classList.add('show-map');
        setFab();
        setTimeout(() => {
          map.invalidateSize();
          const m = markers.get(r.id);
          programmaticMove = true;
          if (m) cluster.zoomToShowLayer(m, () => m.openPopup());
          else map.setView([r.lat, r.lng], 16);
        }, 60);
      }
    });
  }

  // ----------------------------------------------------------- popovers / UI
  const TYPES_ORDER = ['Casa', 'Townhouse / Villa', 'Departamento / Condominio'];
  function countBy(fn) {
    const m = new Map();
    for (const r of state.all) for (const k of [].concat(fn(r))) if (k != null) m.set(k, (m.get(k) || 0) + 1);
    return m;
  }

  function popContent(name) {
    const f = state.f;
    const ccyTxt = state.fx ? state.ccy : 'moneda original';
    if (name === 'price') {
      return `<h3>Rango de precio (${esc(ccyTxt)})</h3>
        <div class="row"><label class="field">Mínimo<input type="number" min="0" step="50000" data-k="priceMin" value="${f.priceMin ?? ''}" placeholder="Sin mínimo"></label>
        <label class="field">Máximo<input type="number" min="0" step="50000" data-k="priceMax" value="${f.priceMax ?? ''}" placeholder="Sin máximo"></label></div>
        <p class="hint">Las propiedades sin precio publicado se ocultan al filtrar por precio.</p>`;
    }
    if (name === 'beds' || name === 'baths') {
      const k = name;
      const opts = [0, 1, 2, 3, 4, 5];
      return `<h3>${name === 'beds' ? 'Recámaras' : 'Baños'}</h3><div class="seg">${opts.map((o) => `<button type="button" data-seg="${k}" data-v="${o}" class="${f[k] === o ? 'on' : ''}">${o ? `${o}+` : 'Cualquiera'}</button>`).join('')}</div>`;
    }
    if (name === 'type') {
      const c = countBy((r) => r.property_type);
      const keys = [...c.keys()].sort((a, b) => (TYPES_ORDER.indexOf(a) + 99 * (TYPES_ORDER.indexOf(a) < 0)) - (TYPES_ORDER.indexOf(b) + 99 * (TYPES_ORDER.indexOf(b) < 0)));
      return `<h3>Tipo de propiedad</h3><div class="checks">${keys.map((k) => `<label><input type="checkbox" data-set="types" value="${esc(k)}" ${f.types.has(k) ? 'checked' : ''}>${esc(k)}<small>${c.get(k)}</small></label>`).join('') || '<p class="hint">Sin datos</p>'}</div>`;
    }
    if (name === 'area') {
      return `<h3>Superficie</h3>
        <div class="row"><label class="field">m² construcción mín.<input type="number" min="0" data-k="m2Min" value="${f.m2Min ?? ''}"></label>
        <label class="field">m² construcción máx.<input type="number" min="0" data-k="m2Max" value="${f.m2Max ?? ''}"></label></div>
        <div class="row" style="margin-top:10px"><label class="field">m² terreno mín.<input type="number" min="0" data-k="landMin" value="${f.landMin ?? ''}"></label></div>
        <p class="hint">Se ocultan los anuncios que no especifican la superficie filtrada.</p>`;
    }
    if (name === 'zone') {
      const c = countBy((r) => r.zone);
      const keys = [...c.keys()].sort((a, b) => a.localeCompare(b, 'es'));
      return `<h3>Colonia / zona</h3><input type="search" placeholder="Buscar zona" data-zone-search style="margin-bottom:10px">
        <div class="checks" data-zone-list>${keys.map((k) => `<label data-name="${esc(k.toLowerCase())}"><input type="checkbox" data-set="zones" value="${esc(k)}" ${f.zones.has(k) ? 'checked' : ''}>${esc(k)}<small>${c.get(k)}</small></label>`).join('') || '<p class="hint">Sin datos</p>'}</div>`;
    }
    if (name === 'source') {
      const c = countBy((r) => sourcesOf(r).map((s) => s.source));
      const keys = [...c.keys()].sort((a, b) => a.localeCompare(b, 'es'));
      return `<h3>Fuente</h3><div class="checks">${keys.map((k) => `<label><input type="checkbox" data-set="sources" value="${esc(k)}" ${f.sources.has(k) ? 'checked' : ''}>${esc(k)}<small>${c.get(k)}</small></label>`).join('') || '<p class="hint">Sin datos</p>'}</div>`;
    }
    return '';
  }

  function closePops() { $$('.pop-panel').forEach((p) => p.remove()); }

  function wirePops() {
    $$('.pop').forEach((pop) => {
      pop.querySelector('.pill').addEventListener('click', (e) => {
        e.stopPropagation();
        const open = pop.querySelector('.pop-panel');
        closePops();
        if (open) return;
        const panel = document.createElement('div');
        panel.className = 'pop-panel';
        panel.innerHTML = popContent(pop.dataset.pop) + '<div class="actions"><button class="btn-text" type="button" data-clear>Limpiar</button><button class="btn-primary" type="button" data-done>Listo</button></div>';
        pop.appendChild(panel);
        const first = panel.querySelector('input,button'); if (first) first.focus();
        const onNum = debounce((inp) => { const v = inp.value === '' ? null : Number(inp.value); state.f[inp.dataset.k] = Number.isFinite(v) && v >= 0 ? v : null; applyFilters(); }, 250);
        panel.addEventListener('input', (ev) => {
          const t = ev.target;
          if (t.dataset.k) onNum(t);
          if (t.hasAttribute('data-zone-search')) {
            const q = t.value.trim().toLowerCase();
            panel.querySelectorAll('[data-zone-list] label').forEach((l) => { l.hidden = q && !l.dataset.name.includes(q); });
          }
        });
        panel.addEventListener('change', (ev) => {
          const t = ev.target;
          if (t.dataset.set) { const s = state.f[t.dataset.set]; t.checked ? s.add(t.value) : s.delete(t.value); applyFilters(); }
        });
        panel.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const seg = ev.target.closest('[data-seg]');
          if (seg) { state.f[seg.dataset.seg] = Number(seg.dataset.v); panel.querySelectorAll('[data-seg]').forEach((b) => b.classList.toggle('on', b === seg)); applyFilters(); }
          if (ev.target.closest('[data-done]')) closePops();
          if (ev.target.closest('[data-clear]')) { clearFilter(pop.dataset.pop); closePops(); }
        });
      });
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.pop-panel')) closePops(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePops(); });
  }

  function clearFilter(name) {
    const f = state.f;
    if (name === 'price') { f.priceMin = f.priceMax = null; }
    if (name === 'beds') f.beds = 0;
    if (name === 'baths') f.baths = 0;
    if (name === 'type') f.types.clear();
    if (name === 'area') { f.m2Min = f.m2Max = f.landMin = null; }
    if (name === 'zone') f.zones.clear();
    if (name === 'source') f.sources.clear();
    if (name === 'bounds') state.bounds = null;
    if (name === 'favs') state.favsOnly = false;
    applyFilters();
  }

  function pillLabels() {
    const f = state.f;
    const cur = state.fx ? state.ccy : '';
    const fmtP = (v) => short(v);
    return {
      price: f.priceMin != null || f.priceMax != null ? `${f.priceMin != null ? fmtP(f.priceMin) : '$0'} – ${f.priceMax != null ? fmtP(f.priceMax) : 'sin máx.'} ${cur}` : null,
      beds: f.beds ? `${f.beds}+ recámaras` : null,
      baths: f.baths ? `${f.baths}+ baños` : null,
      type: f.types.size ? (f.types.size === 1 ? [...f.types][0] : `Tipo (${f.types.size})`) : null,
      area: f.m2Min != null || f.m2Max != null || f.landMin != null ? `${f.m2Min ?? 0}–${f.m2Max ?? '∞'} m²${f.landMin != null ? ` · terreno ≥${f.landMin}` : ''}` : null,
      zone: f.zones.size ? (f.zones.size === 1 ? [...f.zones][0] : `Zonas (${f.zones.size})`) : null,
      source: f.sources.size ? (f.sources.size === 1 ? [...f.sources][0] : `Fuentes (${f.sources.size})`) : null,
    };
  }
  const DEFAULT_PILL = { price: 'Precio', beds: 'Recámaras', baths: 'Baños', type: 'Tipo', area: 'm²', zone: 'Zona', source: 'Fuente' };

  function renderPills() {
    const labels = pillLabels();
    let any = false;
    $$('.pop').forEach((pop) => {
      const k = pop.dataset.pop;
      const b = pop.querySelector('.pill');
      b.textContent = labels[k] || DEFAULT_PILL[k];
      b.classList.toggle('is-active', !!labels[k]);
      any = any || !!labels[k];
    });
    $('#btn-favs').setAttribute('aria-pressed', state.favsOnly);
    $('#btn-clear').hidden = !(any || state.favsOnly || state.bounds);
  }

  function renderChips() {
    const chips = [];
    if (state.bounds) chips.push(['bounds', 'Zona visible del mapa']);
    if (state.favsOnly) chips.push(['favs', 'Solo favoritos']);
    $('#active-chips').innerHTML = chips.map(([k, t]) => `<span class="chip">${esc(t)}<button type="button" data-chip="${k}" aria-label="Quitar">×</button></span>`).join('');
  }

  function setFab() {
    const showing = document.body.classList.contains('show-map');
    $('#fab span').textContent = showing ? 'Lista' : 'Mapa';
  }

  let toastTimer;
  function toast(msg) {
    let t = $('#toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#222;color:#fff;padding:10px 16px;border-radius:10px;z-index:4000;font-size:14px;box-shadow:0 6px 20px rgba(0,0,0,.25)'; document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  const STATUS_TXT = {
    ok: 'OK', sin_inventario: 'Sin inventario', bloqueado: 'Bloqueada (anti-bot)',
    robots_txt_no_permite: 'robots.txt no lo permite', error: 'Sin respuesta / error', accesible_sin_scraper: 'Accesible, sin scraper',
    parcial_bloqueado: 'Parcial (pidió verificación humana)',
  };

  function openSourcesPanel() {
    const rep = state.report;
    const meta = state.meta;
    if (!rep) { openModal('<div class="modal-head"><h2>Fuentes</h2><button class="close" data-close>✕</button></div><p>No se encontró data/sources_report.json.</p>'); return; }
    const t = rep.totals || {};
    const rows = (rep.per_source || []).map((s) => `<tr>
      <td><b>${esc(s.source)}</b><div class="small"><a href="${esc(s.site)}" target="_blank" rel="noopener noreferrer">${esc(s.site)}</a></div></td>
      <td class="${s.status === 'ok' ? 'status-ok' : s.status === 'parcial_bloqueado' ? 'status-warn' : 'status-bad'}">${esc(STATUS_TXT[s.status] || s.status)}</td>
      <td>${s.raw_count ?? 0}</td><td>${s.kept ?? 0}</td><td class="small">${esc(fmtDate(s.started_at) || '')}</td>
      <td class="small">${esc([...(s.errors || []).slice(0, 2), ...(s.notes || []).slice(0, 2)].join(' · '))}</td></tr>`).join('');
    openModal(`<div class="modal-head"><div><h2>Fuentes y estado de la última corrida</h2>
      <div class="sub">Corrida: ${esc(fmtDate(rep.generated_at))} · ${t.final_after_dedupe ?? 0} propiedades finales · ${t.pct_coords_exacta ?? 0}% con coordenadas exactas del anuncio</div></div>
      <button class="close" type="button" data-close aria-label="Cerrar">✕</button></div>
      <div class="table-scroll"><table class="src-table"><thead><tr><th>Fuente</th><th>Estado</th><th>Anuncios leídos</th><th>En San Carlos (residencial)</th><th>Consultada</th><th>Notas / errores</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small">Deduplicación: ${rep.dedupe ? `${rep.dedupe.input} anuncios → ${rep.dedupe.output} propiedades (${rep.dedupe.merged_groups} publicadas en más de un portal)` : 'n/d'}.
      Coordenadas: ${t.coords_exacta ?? 0} exactas, ${t.coords_aproximada ?? 0} aproximadas por colonia, ${t.coords_null ?? 0} sin ubicación.</p>
      ${meta && meta.fx ? `<p class="small">Tipo de cambio: 1 USD = ${fmtNum(meta.fx.usd_mxn, 4)} MXN · ${esc(meta.fx.provider)} · fecha ${esc(meta.fx.rate_date)}</p>` : ''}`);
  }

  function wireUi() {
    $$('.ccy button').forEach((b) => b.addEventListener('click', () => {
      if (!state.fx) return;
      const old = state.ccy;
      state.ccy = b.dataset.ccy;
      LS.set('sc_ccy', state.ccy);
      $$('.ccy button').forEach((x) => x.classList.toggle('on', x === b));
      // Convertir rango de precio activo a la nueva moneda
      if (old !== state.ccy && state.fx) {
        const k = state.ccy === 'USD' ? 1 / state.fx.usd_mxn : state.fx.usd_mxn;
        if (state.f.priceMin != null) state.f.priceMin = Math.round(state.f.priceMin * k);
        if (state.f.priceMax != null) state.f.priceMax = Math.round(state.f.priceMax * k);
      }
      applyFilters();
      renderCompareBar();
    }));
    const sortSel = $('#sort');
    sortSel.value = state.sort;
    sortSel.addEventListener('change', () => { state.sort = sortSel.value; LS.set('sc_sort', state.sort); applyFilters(); });
    $('#btn-favs').addEventListener('click', () => { state.favsOnly = !state.favsOnly; applyFilters(); });
    $('#btn-clear').addEventListener('click', () => {
      ['price', 'beds', 'baths', 'type', 'area', 'zone', 'source'].forEach((n) => clearFilter(n));
      state.bounds = null; state.favsOnly = false; applyFilters();
    });
    $('#active-chips').addEventListener('click', (e) => { const b = e.target.closest('[data-chip]'); if (b) clearFilter(b.dataset.chip); });
    $('#fab').addEventListener('click', () => {
      document.body.classList.toggle('show-map');
      setFab();
      setTimeout(() => map.invalidateSize(), 50);
    });
    $('#btn-compare').addEventListener('click', openCompare);
    $('#compare-items').addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) toggleCompare(b.dataset.rm); });
    $('#btn-sources').addEventListener('click', openSourcesPanel);
  }

  function renderHeader() {
    const m = state.meta;
    const nSrc = m ? new Set(state.all.flatMap((r) => sourcesOf(r).map((s) => s.source))).size : 0;
    $('#meta-count').textContent = m ? `${fmtNum(state.all.length)} propiedades de ${nSrc} fuente${nSrc === 1 ? '' : 's'}` : 'Sin datos';
    $('#meta-updated').textContent = m && m.generated_at ? `Última actualización: ${fmtDate(m.generated_at)}` : 'Última actualización: —';
    const fxl = $('#fxline');
    if (state.fx) {
      fxl.textContent = `Tipo de cambio: 1 USD = ${fmtNum(state.fx.usd_mxn, 4)} MXN · ${state.fx.provider} · fecha del tipo de cambio: ${state.fx.rate_date}. Los precios convertidos son aproximados (≈).`;
    } else {
      fxl.textContent = 'Tipo de cambio no disponible en la última actualización: cada precio se muestra en la moneda en que se publicó.';
      $$('.ccy button').forEach((b) => { b.disabled = true; b.classList.remove('on'); });
    }
    $$('.ccy button').forEach((b) => b.classList.toggle('on', !!state.fx && b.dataset.ccy === state.ccy));
  }

  // ------------------------------------------------------------------- init
  async function loadJson(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  }

  async function init() {
    initMap();
    wireGrid();
    wireModal();
    wirePops();
    wireUi();
    let data = null;
    // Versión de un solo archivo: los datos vienen incrustados en la página
    const embedded = window.__SC_DATA__ || null;
    if (embedded) {
      data = embedded;
      state.report = window.__SC_REPORT__ || null;
    } else {
      try { data = await loadJson('data/listings.json'); } catch (e) { console.warn(e); }
      try { state.report = await loadJson('data/sources_report.json'); } catch { /* opcional */ }
    }
    if (!embedded && location.protocol === 'file:') {
      $('#empty').hidden = false;
      $('#empty').innerHTML = '<h2>Abre el sitio con un servidor local</h2><p>Los navegadores no permiten leer <code>data/listings.json</code> desde <code>file://</code>. Corre <code>npm run serve</code> y abre <code>http://localhost:8080</code>.</p>';
    }
    state.meta = data;
    state.fx = data && data.fx && data.fx.usd_mxn ? data.fx : null;
    state.all = (data && Array.isArray(data.listings) ? data.listings : []).filter((r) => r && r.source_url);
    state.all.forEach((r) => state.byId.set(r.id, r));
    renderHeader();
    applyFilters();
    renderCompareBar();
    fitAll();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
