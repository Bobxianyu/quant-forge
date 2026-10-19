/* 使用已保存的真实快照验收页面，不触发任何新的采集。 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const response = await page.request.get('http://127.0.0.1:8765/api/market');
    const market = await response.json();
    const stocks = market.modules.stocks.snapshot.data.items;
    assert.ok(stocks.length > 5000);
    assert.equal(market.modules.indices.snapshot.data.items.length, 4);
    assert.ok(market.modules.sectors.snapshot.data.items.length > 0);
    await page.goto('http://127.0.0.1:8765');
    await page.waitForFunction(() => document.querySelectorAll('#stock-rows button').length > 0);
    assert.equal(await page.locator('#stock-rows tr').count(), 50);
    const sorted = stocks.filter((stock) => Number.isFinite(stock.previousTurnover))
      .sort((left, right) => right.previousTurnover - left.previousTurnover || left.code.localeCompare(right.code));
    assert.match(await page.locator('#stock-rows tr').first().innerText(), new RegExp(sorted[0].code));
    await page.locator('#query').fill('600519.SH');
    assert.equal(await page.locator('#stock-rows tr').count(), 1);
    await page.locator('#stock-rows button').first().click();
    const detail = await page.locator('#detail-body').innerText();
    assert.match(detail, /全部行业归属/);
    assert.match(detail, /全部概念归属/);
    assert.match(detail, /行情时点/);
    await page.locator('#close-detail').click();
    await page.locator('#reset').click();
    await page.screenshot({ path: 'outputs/live-market-workbench.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ stocks: stocks.length, sectors: market.modules.sectors.snapshot.data.items.length,
      indices: 4, coverage: market.modules.stocks.snapshot.data.coverage, consoleErrors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
