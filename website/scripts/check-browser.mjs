import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const { chromium } = await import(pathToFileURL(process.argv[2]).href);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('https://www.cqtcshequ.com/', { waitUntil: 'networkidle' });
    await page.locator('h1').waitFor();
    assert.ok(await page.locator('#video-download-guide').count());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.getByRole('button', { name: '下载 Windows 版', exact: true }).click();
    await page.locator('dialog[open]').waitFor();
    assert.ok((await page.getByRole('link', { name: '立即下载 ZIP' }).getAttribute('href')).endsWith('.zip'));
    assert.deepEqual(errors, []);
    console.log(`Live ${width}px: no overflow or runtime errors; download dialog works.`);
    await page.close();
  }
  const page = await browser.newPage({ javaScriptEnabled: false });
  await page.goto('https://www.cqtcshequ.com/');
  assert.ok(await page.locator('#video-download-guide').count());
  console.log('Live JavaScript-disabled: full SEO guide present.');
} finally { await browser.close(); }
