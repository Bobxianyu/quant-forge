/* 使用隔离测试响应验证浏览器交互，不向真实服务发送采集请求。 */
const { chromium } = require('playwright');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const STATIC_DIR = path.resolve(__dirname, '../../src/quant_forge/web/static');
const BASE_URL = 'http://127.0.0.1:8765';

/** 验证初次只读、分页、归属详情、刷新令牌和窄屏布局。 */
async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let refreshCount = 0;
    let readCount = 0;
    let offline = false;
    let postMode = 'accepted';
    let activeReads = 0;
    const stocks = Array.from({ length: 123 }, (_, index) => ({
      code: String(600000 + index), name: `测试股票${index}`, previousTurnover: index * 100000000,
      todayTurnover: index === 0 ? null : 0, lastPrice: 10, returnPct: 0, status: 'unknown',
    }));
    const snapshot = (items) => ({ snapshotId: '测试快照', source: '浏览器隔离测试',
      collectedAt: '2026-09-16T10:00:00+08:00', data: { items } });
    await page.route(`${BASE_URL}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/session') return route.fulfill({ json: { token: 'test-session-token' } });
      if (url.pathname === '/api/market/refresh') {
        assert.equal(route.request().headers()['x-csrf-token'], 'test-session-token');
        refreshCount += 1;
        activeReads = postMode === 'accepted' ? 0 : 1;
        if (postMode === 'conflict') return route.fulfill({ status: 409, json: { error: '已有任务' } });
        if (postMode === 'disconnected') return route.abort('failed');
        return route.fulfill({ status: 202, json: { refreshing: true } });
      }
      if (url.pathname === '/api/market') {
        readCount += 1;
        if (offline) return route.abort('failed');
        return route.fulfill({ json: { refreshing: activeReads-- > 0, modules: {
          indices: { snapshot: snapshot([]) }, stocks: { snapshot: snapshot(stocks) },
          sectors: { snapshot: snapshot([
            { code: 'a', name: '<img src=x onerror=alert(1)>', type: 'industry', members: ['600122'] },
            { code: 'b', name: '测试概念', type: 'concept', members: ['600122'] },
            { code: 'c', name: '第二概念', type: 'concept', members: ['600122'] },
          ]) },
        } } });
      }
      const file = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
      const contentType = file.endsWith('.css') ? 'text/css' : file.endsWith('.js')
        ? 'application/javascript' : 'text/html';
      return route.fulfill({ body: await readFile(path.join(STATIC_DIR, file)), contentType });
    });
    await page.goto(BASE_URL);
    await page.waitForFunction(() => document.getElementById('stock-count').textContent.includes('123'));
    assert.equal(refreshCount, 0);
    assert.equal(await page.locator('#stock-rows tr').count(), 50);
    assert.match(await page.locator('#stock-rows tr').first().innerText(), /600122/);
    assert.equal(await page.locator('#stock-rows img').count(), 0);
    await page.locator('#stock-rows button').first().click();
    assert.match(await page.locator('#detail-body').innerText(), /第二概念/);
    await page.locator('#close-detail').click();
    await page.locator('#next').click();
    await page.locator('#next').click();
    assert.equal(await page.locator('#stock-rows tr').count(), 23);
    await page.locator('#query').fill('不存在');
    assert.match(await page.locator('#stock-rows').innerText(), /没有符合条件/);
    await page.locator('#reset').click();
    await page.locator('#refresh').click();
    await page.waitForFunction(() => !document.getElementById('refresh').disabled);
    assert.equal(refreshCount, 1);
    assert.equal(readCount, 2);
    offline = true;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.getElementById('connection').textContent.includes('无法读取'));
    assert.equal(await page.locator('#stock-rows tr').count(), 50);
    offline = false;
    await page.waitForFunction(() => !document.getElementById('refresh').disabled, null, { timeout: 5000 });
    // 提交结果不明确或冲突时也应只读跟进服务状态，绝不自动重提刷新。
    for (const mode of ['conflict', 'disconnected']) {
      postMode = mode;
      const previousReads = readCount;
      const previousPosts = refreshCount;
      await page.locator('#refresh').click();
      await page.waitForFunction(() => document.getElementById('refresh').textContent === '采集中…',
        null, { timeout: 5000 });
      assert.match(await page.locator('#connection').innerText(), /刷新请求失败/);
      await page.waitForFunction(() => !document.getElementById('refresh').disabled, null, { timeout: 5000 });
      assert.ok(readCount >= previousReads + 2);
      assert.equal(refreshCount, previousPosts + 1);
      assert.match(await page.locator('#connection').innerText(), /已读取本地保存/);
    }
    // 首次 GET 中断后恢复时，自动重试只能读取已有数据。
    offline = true;
    const previousPosts = refreshCount;
    await page.reload();
    await page.waitForFunction(() => document.getElementById('connection').textContent.includes('无法读取'));
    offline = false;
    await page.waitForFunction(() => document.getElementById('stock-count').textContent.includes('123'),
      null, { timeout: 5000 });
    assert.equal(refreshCount, previousPosts);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('浏览器验证通过：只读启动、123只分页、完整归属、安全文本、手动刷新、离线保留、窄屏布局。');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error('浏览器验证失败：', error); process.exitCode = 1; });
