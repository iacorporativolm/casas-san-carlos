// Imprime el texto visible de una página ya renderizada (para sitios que pintan con JS).
// Uso: node tests/render_text.js <url>
let chromium;
try { ({ chromium } = require('playwright')); } catch { ({ chromium } = require('/opt/node22/lib/node_modules/playwright')); }
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 SanCarlosCasasBot/1.0', locale: 'es-MX' });
  try {
    await p.goto(process.argv[2], { waitUntil: 'domcontentloaded', timeout: 60000 });
    // Espera a que el sitio pinte los datos (algunos nunca llegan a "networkidle" por chats/analytics)
    await p.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await p.waitForTimeout(2500);
    process.stdout.write(await p.evaluate(() => document.body.innerText));
  } catch (e) { process.stderr.write(String(e)); process.exitCode = 1; }
  await b.close();
})();
