/* 正式行情界面：仅由用户触发采集，轮询只读取后端状态。 */
(function () {
  'use strict';
  const { filterStocks, paginate, membershipMap, formatNumber, YUAN_PER_YI } = window.MarketView;
  const POLL_INTERVAL_MS = 2000;
  const INDEX_NAMES = {
    '883994.TI': '昨日打首板表现', '883997.TI': '昨日非ST二板表现',
    '883410.TI': '最近多板', '883900.TI': '昨日涨停表现',
  };
  const MODULE_NAMES = { indices: '市场指数', stocks: '股票行情', sectors: '行业与概念' };
  const state = {
    modules: {}, page: 1, memberships: new Map(), timer: null,
    refreshing: false, requesting: false, readFailed: false, requestError: '',
  };
  const byId = (id) => document.getElementById(id);

  /** 使用文本节点显示外部数据，防止行情字段注入页面。 */
  function element(tag, text, className = '') {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    node.className = className;
    return node;
  }

  /** 返回模块快照中的实际数据；没有快照时保持空集合。 */
  function items(key) {
    return state.modules[key]?.snapshot?.data?.items || [];
  }

  /** 格式化涨跌幅，真实零值使用中性颜色。 */
  function percent(value) {
    return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${formatNumber(value)}%` : '缺失';
  }

  /** 根据数值选择涨跌色，缺失值保持中性。 */
  function valueClass(value) {
    return Number.isFinite(value) ? (value > 0 ? 'up' : value < 0 ? 'down' : '') : 'muted';
  }

  /** 将来源时间统一按北京时间显示，无法识别的时间保留原文。 */
  function time(value) {
    if (!value) return '缺失';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value)
      : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
  }

  /** 显示四项指数及各自行情时点，没有数据时显示缺失。 */
  function renderIndices() {
    const values = items('indices');
    const cards = Object.entries(INDEX_NAMES).map(([code, name]) => {
      const item = values.find((value) => value.code === code) || {};
      const card = element('article', undefined, 'index-card');
      card.append(element('h3', item.name || name),
        element('strong', percent(item.valuePct), valueClass(item.valuePct)),
        element('p', `行情时点：${time(item.quoteAt)}`, 'muted'));
      return card;
    });
    byId('indices').replaceChildren(...cards);
  }

  /** 根据模块独立展示错误、来源与快照，旧快照在失败时继续可见。 */
  function renderModules() {
    const cards = Object.entries(MODULE_NAMES).map(([key, name]) => {
      const module = state.modules[key] || {};
      const snapshot = module.snapshot;
      const card = element('article', undefined, 'module-card');
      card.append(element('h3', name), element('p', snapshot ? '已有成功快照' : '尚无成功快照', 'muted'));
      if (module.error) card.append(element('p', `最近采集失败：${module.error}`, 'error'));
      if (snapshot) {
        card.append(element('p', `来源：${snapshot.source || '缺失'}`),
          element('p', `采集时间：${time(snapshot.collectedAt)}`), element('p', `快照：${snapshot.snapshotId}`));
      }
      card.append(element('p', `最近尝试：${time(module.lastAttemptAt)}`));
      if (key === 'sectors') {
        card.append(element('p', `归属时点：${time(snapshot?.data?.membershipAsOf)}`));
      }
      if (key === 'stocks' && snapshot?.data?.coverage) {
        const coverage = snapshot.data.coverage;
        card.append(element('p', `报价覆盖：${coverage.received ?? '缺失'} / ${coverage.expected ?? '缺失'} 只`),
          element('p', `昨日成交额有效：${coverage.previousTurnover ?? '缺失'} 只；`
            + `今日成交额有效：${coverage.currentTurnover ?? '缺失'} 只`),
          element('p', `行情时间口径：${coverage.quoteTimeBasis || '缺失'}`));
        if (coverage.historySource) card.append(element('p', `昨日成交额来源：${coverage.historySource}`));
        if (coverage.complete === false) card.append(element('p', '报价覆盖不完整，请留意缺失字段。', 'error'));
      }
      return card;
    });
    byId('modules').replaceChildren(...cards);
  }

  /** 更新板块选择器，保持用户已选择的行业或概念。 */
  function renderSectors() {
    const selected = byId('sector').value;
    const choices = [element('option', '全部板块')];
    choices[0].value = '';
    for (const sector of items('sectors')) {
      const option = element('option', `${sector.type === 'industry' ? '行业' : '概念'} · ${sector.name}`);
      option.value = `${sector.type}:${sector.code}`;
      choices.push(option);
    }
    byId('sector').replaceChildren(...choices);
    byId('sector').value = selected;
    state.memberships = membershipMap(items('sectors'));
  }

  /** 展示股票全部行业和概念归属及数据时点。 */
  function showDetail(stock) {
    const body = byId('detail-body');
    const data = state.modules.stocks?.snapshot?.data || {};
    byId('detail-title').textContent = `${stock.name} · ${stock.code}`;
    const lines = [
      `最新价：${formatNumber(stock.lastPrice)} 元`, `涨跌幅：${percent(stock.returnPct)}`,
      `昨日成交额：${formatNumber(stock.previousTurnover, YUAN_PER_YI)} 亿元`,
      `今日成交额：${formatNumber(stock.todayTurnover, YUAN_PER_YI)} 亿元`,
      `昨日交易日：${data.previousTradeDate || '缺失'}`,
      `采集基准日：${stock.tradeDate || data.tradeDate || '缺失'}`,
      `行情时点：${time(stock.quoteAt)}`, '交易状态：未知',
    ];
    body.replaceChildren(...lines.map((line) => element('p', line)));
    const memberships = state.memberships.get(stock.code) || [];
    for (const type of ['industry', 'concept']) {
      const names = memberships.filter((sector) => sector.type === type).map((sector) => sector.name);
      body.append(element('h3', type === 'industry' ? '全部行业归属' : '全部概念归属'),
        element('p', names.length ? names.join('、') : '归属数据缺失'));
    }
    body.append(element('p', `归属时点：${time(state.modules.sectors?.snapshot?.data?.membershipAsOf)}`, 'muted'));
    byId('detail').showModal();
  }

  /** 按当前条件绘制一页股票，全部股票都可通过分页访问。 */
  function renderStocks() {
    const options = Object.fromEntries(['query', 'sector', 'minimum', 'sort', 'direction']
      .map((key) => [key, byId(key).value]));
    const all = items('stocks');
    const result = paginate(filterStocks(all, options, state.memberships), state.page);
    state.page = result.page;
    const rows = result.items.map((stock) => {
      const row = element('tr');
      const identity = element('td');
      identity.append(element('strong', stock.name), element('small', stock.code));
      const sectors = state.memberships.get(stock.code) || [];
      const brief = sectors.slice(0, 2).map((sector) => sector.name).join(' / ');
      const cell = element('td', brief ? `${brief}${sectors.length > 2 ? ` 等${sectors.length}项` : ''}` : '归属缺失');
      const action = element('td');
      const button = element('button', '查看', 'text-button');
      button.type = 'button';
      button.setAttribute('aria-label', `查看${stock.name}详情`);
      button.addEventListener('click', () => showDetail(stock));
      action.append(button);
      row.append(identity, element('td', formatNumber(stock.lastPrice)),
        element('td', percent(stock.returnPct), valueClass(stock.returnPct)),
        element('td', formatNumber(stock.previousTurnover, YUAN_PER_YI)),
        element('td', formatNumber(stock.todayTurnover, YUAN_PER_YI)), cell, element('td', '未知', 'muted'), action);
      return row;
    });
    if (!rows.length) {
      const cell = element('td', all.length ? '没有符合条件的股票，请调整筛选条件。' : '尚无股票行情，请点击刷新行情。');
      cell.colSpan = 8;
      const row = element('tr');
      row.append(cell);
      rows.push(row);
    }
    byId('stock-rows').replaceChildren(...rows);
    byId('stock-count').textContent = `沪深 A 股 · 已收录 ${all.length} 只`;
    byId('page-summary').textContent = `筛选结果 ${result.total} 只 · 第 ${result.page} / ${result.pages} 页 · 每页 50 只`;
    byId('previous').disabled = result.page <= 1;
    byId('next').disabled = result.page >= result.pages;
    const data = state.modules.stocks?.snapshot?.data || {};
    byId('stock-dates').textContent = `昨日交易日：${data.previousTradeDate || '缺失'} · `
      + `今日成交额日期：${data.tradeDate || '缺失'} · 默认按昨日成交额降序；0 亿元下限保留缺失值。`;
  }

  /** 请求 JSON 并提供可读的连接错误，不覆盖现有行情。 */
  async function request(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    if (!response.ok) throw new Error(`服务返回 ${response.status}`);
    return response.json();
  }

  /** 后台刷新期间继续读取状态；轮询本身不会启动任何采集。 */
  function scheduleRead() {
    clearTimeout(state.timer);
    if (state.refreshing || state.readFailed) state.timer = setTimeout(readMarket, POLL_INTERVAL_MS);
  }

  /** 获取已有行情并逐模块显示成功结果，失败时保留上次画面。 */
  async function readMarket() {
    try {
      const response = await request('/api/market');
      if (!response.modules || typeof response.refreshing !== 'boolean') throw new Error('行情响应格式无效');
      state.modules = response.modules;
      state.refreshing = response.refreshing;
      state.readFailed = false;
      renderIndices();
      renderSectors();
      renderStocks();
      renderModules();
      const status = state.refreshing
        ? '正在采集真实行情，已完成模块会先显示。首次全市场采集耗时较长，请耐心等待。'
        : '已读取本地保存的行情。需要最新数据时，请点击刷新行情。';
      byId('connection').textContent = `${state.requestError}${status}`;
    } catch (error) {
      state.readFailed = true;
      byId('connection').textContent = `${state.requestError}无法读取行情：${error.message}。`
        + '已保留当前页面数据，将自动重试读取服务状态。';
    } finally {
      byId('refresh').disabled = state.refreshing || state.requesting;
      byId('refresh').textContent = state.refreshing ? '采集中…' : '刷新行情 ↗';
      scheduleRead();
    }
  }

  /** 取得会话令牌后发起一次手动刷新，使用后端状态判断完成。 */
  async function refreshMarket() {
    clearTimeout(state.timer);
    state.requesting = true;
    state.requestError = '';
    byId('refresh').disabled = true;
    byId('connection').textContent = '正在提交刷新请求…';
    try {
      const session = await request('/api/session');
      if (!session.token) throw new Error('会话令牌缺失，请重新打开页面');
      await request('/api/market/refresh', { method: 'POST', headers: { 'X-CSRF-Token': session.token } });
      state.refreshing = true;
    } catch (error) {
      state.requestError = `刷新请求失败：${error.message}。正在确认后台任务状态；不会自动重新提交刷新。`;
      byId('connection').textContent = state.requestError;
    } finally {
      state.requesting = false;
      byId('refresh').disabled = state.refreshing;
    }
    // POST 冲突或响应断线也可能已有任务运行，必须通过 GET 确认真实状态。
    await readMarket();
  }

  byId('filters').addEventListener('submit', (event) => event.preventDefault());
  byId('filters').addEventListener('input', () => { state.page = 1; renderStocks(); });
  byId('reset').addEventListener('click', () => {
    HTMLFormElement.prototype.reset.call(byId('filters'));
    state.page = 1;
    renderStocks();
  });
  byId('previous').addEventListener('click', () => { state.page -= 1; renderStocks(); });
  byId('next').addEventListener('click', () => { state.page += 1; renderStocks(); });
  byId('refresh').addEventListener('click', refreshMarket);
  byId('close-detail').addEventListener('click', () => byId('detail').close());
  renderIndices();
  renderStocks();
  renderModules();
  readMarket();
})();
