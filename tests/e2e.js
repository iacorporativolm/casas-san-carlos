// Prueba end-to-end del sitio con Playwright (Chromium headless).
// Uso: npm run serve (en otra terminal) && node tests/e2e.js [http://localhost:8080] [carpeta_capturas]
const path = require('path');
let chromium;
try { ({ chromium } = require('playwright')); } catch { ({ chromium } = require('/opt/node22/lib/node_modules/playwright')); }

const BASE = process.argv[2] || 'http://localhost:8080';
const OUT = process.argv[3] || path.join(__dirname, 'screenshots');
require('fs').mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, info = '') => { results.push({ name, ok: !!ok, info }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? ' — ' + info : ''}`); };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|ERR_|net::/.test(m.text())) errors.push(m.text()); });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.card', { timeout: 20000 });
  await page.waitForTimeout(800);

  const total = await page.$$eval('.card', (c) => c.length);
  const meta = (await page.textContent('#meta-count')).trim();
  const updated = (await page.textContent('#meta-updated')).trim();
  check('Tarjetas renderizadas', total > 0, `${total} tarjetas`);
  check('Encabezado "X propiedades de Y fuentes"', /\d+ propiedades de \d+ fuentes?/.test(meta), meta);
  check('Encabezado "Última actualización"', /Última actualización: .*\d/.test(updated), updated);
  check('Línea de tipo de cambio con fecha', /1 USD = [\d.,]+ MXN .*fecha/.test(await page.textContent('#fxline')));
  const pins = await page.$$eval('.price-pin, .cluster-pin', (p) => p.length);
  check('Pines/clusters en el mapa', pins > 0, `${pins} marcadores visibles`);
  const allLinks = await page.$$eval('.card .orig a', (as) => as.every((a) => /^https?:\/\//.test(a.href)));
  check('Cada tarjeta tiene "Ver anuncio original" con URL', allLinks);
  await page.screenshot({ path: path.join(OUT, '01-desktop.png') });

  // Hover tarjeta -> pin resaltado
  const firstWithPin = await page.$('.card:not(:has(.approx-tag))');
  if (firstWithPin) {
    await firstWithPin.hover();
    await page.waitForTimeout(200);
    const hot = await page.$$eval('.leaflet-marker-icon.is-hot', (e) => e.length);
    check('Hover en tarjeta resalta su pin/cluster', hot === 1);
  }
  // Hover pin -> tarjeta resaltada
  const pin = await page.$('.price-pin');
  if (pin) {
    await pin.hover();
    await page.waitForTimeout(200);
    check('Hover en pin resalta su tarjeta', (await page.$$eval('.card.is-hot', (e) => e.length)) === 1);
  }

  // Filtro recámaras 3+
  await page.click('.pop[data-pop="beds"] .pill');
  await page.click('[data-seg="beds"][data-v="3"]');
  await page.waitForTimeout(300);
  const beds = await page.$$eval('.card .specs', (s) => s.map((x) => x.textContent));
  const okBeds = beds.every((t) => { const m = /([\d.]+) rec\./.exec(t); return m && parseFloat(m[1]) >= 3; });
  check('Filtro recámaras 3+', beds.length > 0 && okBeds, `${beds.length} resultados`);
  await page.click('[data-done]');
  // Filtro precio (MXN)
  await page.click('.pop[data-pop="price"] .pill');
  await page.fill('[data-k="priceMax"]', '8000000');
  await page.waitForTimeout(600);
  const prices = await page.$$eval('.card .price b', (b) => b.map((x) => x.textContent));
  const okPrice = prices.every((t) => { const n = parseFloat(t.replace(/[^\d.]/g, '')); return n <= 8000000; });
  check('Filtro precio máx. 8M MXN', prices.length > 0 && okPrice, `${prices.length} resultados`);
  await page.click('[data-done]');
  await page.screenshot({ path: path.join(OUT, '02-filtros.png') });
  await page.click('#btn-clear');
  await page.waitForTimeout(300);
  check('Limpiar filtros restaura el total', (await page.$$eval('.card', (c) => c.length)) === total);

  // Filtro tipo: Terreno
  await page.click('.pop[data-pop="type"] .pill');
  const landBox = await page.$('[data-set="types"][value="Terreno"]');
  if (landBox) {
    await landBox.check();
    await page.waitForTimeout(300);
    const specs = await page.$$eval('.card .specs', (s) => s.map((x) => x.textContent.trim()));
    check('Filtro tipo Terreno', specs.length > 0 && specs.every((t) => t.startsWith('Terreno')), `${specs.length} terrenos`);
    const pins = await page.$$eval('.price-pin.land, .cluster-pin', (p) => p.length);
    check('Pines de terreno en el mapa', pins > 0);
    await page.click('[data-clear]');
  } else {
    check('Filtro tipo Terreno', false, 'no hay opción Terreno');
  }
  await page.waitForTimeout(200);

  // Orden precio ascendente
  await page.selectOption('#sort', 'price_asc');
  await page.waitForTimeout(300);
  // Solo precios convertibles (los que no indican moneda van al final a propósito)
  const asc = await page.$$eval('.card .price', (ps) => ps.filter((p) => !/no especificad/.test(p.textContent)).map((p) => parseFloat(p.querySelector('b').textContent.replace(/[^\d.]/g, ''))).filter((n) => !Number.isNaN(n)));
  check('Orden precio menor a mayor', asc.every((v, i) => i === 0 || v >= asc[i - 1]));
  // Toggle USD
  await page.click('.ccy button[data-ccy="USD"]');
  await page.waitForTimeout(300);
  check('Toggle USD cambia precios', /US\$/.test(await page.textContent('.card .price b')));
  await page.click('.ccy button[data-ccy="MXN"]');
  await page.selectOption('#sort', 'recent');

  // Detalle
  await page.click('.card .card-body');
  await page.waitForSelector('#modal[open]');
  const modalTxt = await page.textContent('#modal-body');
  check('Detalle abre con datos y links', /Ver anuncio original/.test(modalTxt) && /(Publicado en \d+ portal|\d+ anuncios en \d+ portal)/.test(modalTxt));
  await page.screenshot({ path: path.join(OUT, '03-detalle.png') });
  await page.click('#modal [data-close]');

  // Favoritos
  await page.click('.card .fav');
  await page.click('#btn-favs');
  await page.waitForTimeout(200);
  check('Favoritos (localStorage) filtra 1', (await page.$$eval('.card', (c) => c.length)) === 1);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('sc_favs') || '[]').length);
  check('Favorito guardado en localStorage', stored === 1);
  await page.click('#btn-favs');

  // Comparar
  const cmps = await page.$$('.card .cmp input');
  for (const c of cmps.slice(0, 3)) await c.check();
  await page.click('#btn-compare');
  await page.waitForSelector('.cmp-table');
  check('Comparador con 3 propiedades', (await page.$$eval('.cmp-table tr:nth-child(2) td', (t) => t.length)) === 3);
  await page.screenshot({ path: path.join(OUT, '04-comparar.png') });
  await page.click('#modal [data-close]');

  // Buscar en esta zona del mapa
  await page.evaluate(() => window.scrollTo(0, 0));
  const map = await page.$('#map');
  const box = await map.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(800);
  const btnVisible = await page.isVisible('#search-area');
  check('Botón "Buscar en esta zona del mapa" aparece al mover el mapa', btnVisible);
  if (btnVisible) {
    await page.click('#search-area');
    await page.waitForTimeout(300);
    const n = await page.$$eval('.card', (c) => c.length);
    check('Filtro por zona visible reduce resultados', n <= total, `${n} de ${total}`);
  }

  // Fuentes y estado
  await page.click('#btn-sources');
  await page.waitForSelector('.src-table');
  check('Panel de fuentes y estado', (await page.$$eval('.src-table tbody tr', (r) => r.length)) > 0);
  await page.screenshot({ path: path.join(OUT, '05-fuentes.png') });
  await page.click('#modal [data-close]');

  // Móvil
  const m = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  m.on('pageerror', (e) => errors.push('móvil: ' + e.message));
  await m.goto(BASE, { waitUntil: 'domcontentloaded' });
  await m.waitForSelector('.card');
  await m.waitForTimeout(500);
  const overflow = await m.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check('Móvil sin scroll horizontal', !overflow);
  check('Móvil: botón flotante "Mapa"', await m.isVisible('#fab'));
  await m.screenshot({ path: path.join(OUT, '06-movil-lista.png') });
  await m.click('#fab');
  await m.waitForTimeout(600);
  check('Móvil: el botón muestra el mapa', await m.evaluate(() => document.body.classList.contains('show-map')));
  await m.screenshot({ path: path.join(OUT, '07-movil-mapa.png') });

  check('Sin errores de JavaScript', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} pruebas OK`);
  process.exit(failed.length ? 1 : 0);
})();
