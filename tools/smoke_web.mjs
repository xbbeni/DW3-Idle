// The browser build's smoke test: the page loads, its wasm starts, and its
// status line comes to asking for the disc (a fresh browser has none).
//   node tools/smoke_web.mjs http://localhost:8000/index.html
import puppeteer from 'puppeteer-core';

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME || '/usr/bin/google-chrome',
  args: ['--no-sandbox'],
  headless: true,
});
const page = await browser.newPage();
page.on('console', (m) => console.log('console:', m.text()));
page.on('pageerror', (e) => console.log('pageerror:', e.message));
await page.goto(process.argv[2]);
try {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Primeira vez'),
    { timeout: 120000 });
  console.log('OK:', await page.$eval('#status', (e) => e.textContent));
} catch (e) {
  console.log('FAILED, status:', await page.$eval('#status', (e) => e.textContent));
  process.exitCode = 1;
}
await browser.close();
