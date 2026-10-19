/* 行情纯函数：筛选、排序、分页与显示格式共用同一套规则。 */
(function (root) {
  'use strict';
  const YUAN_PER_YI = 100000000;
  const DEFAULT_PAGE_SIZE = 50;

  /** 按股票代码聚合全部板块归属，保留行业和概念。 */
  function membershipMap(sectors = []) {
    const result = new Map();
    for (const sector of sectors) {
      for (const code of sector.members || []) {
        if (!result.has(code)) result.set(code, []);
        result.get(code).push(sector);
      }
    }
    return result;
  }

  /** 按查询、板块和昨日成交额下限筛选；零下限保留缺失值。 */
  function filterStocks(stocks, options = {}, memberships = new Map()) {
    const query = String(options.query || '').trim().toLowerCase();
    const minimum = Math.max(0, Number(options.minimum) || 0) * YUAN_PER_YI;
    const key = ['previousTurnover', 'todayTurnover', 'returnPct', 'lastPrice'].includes(options.sort)
      ? options.sort : 'previousTurnover';
    const direction = options.direction === 'asc' ? 1 : -1;
    return stocks.filter((stock) => {
      const matchesQuery = `${stock.code} ${stock.name}`.toLowerCase().includes(query);
      const matchesAmount = minimum === 0
        || (Number.isFinite(stock.previousTurnover) && stock.previousTurnover >= minimum);
      const matchesSector = !options.sector || (memberships.get(stock.code) || [])
        .some((sector) => `${sector.type}:${sector.code}` === options.sector);
      return matchesQuery && matchesAmount && matchesSector;
    }).sort((left, right) => {
      // 缺失数据不参与数值比较，无论升序降序均排在末尾。
      const leftValid = Number.isFinite(left[key]);
      const rightValid = Number.isFinite(right[key]);
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      const difference = leftValid ? direction * (left[key] - right[key]) : 0;
      return difference || String(left.code).localeCompare(String(right.code));
    });
  }

  /** 返回有效页及总页数，空列表始终保持第一页。 */
  function paginate(items, requestedPage = 1, requestedSize = DEFAULT_PAGE_SIZE) {
    const size = Math.max(1, Math.floor(Number(requestedSize) || DEFAULT_PAGE_SIZE));
    const pages = Math.max(1, Math.ceil(items.length / size));
    const page = Math.max(1, Math.min(pages, Math.floor(Number(requestedPage) || 1)));
    return { items: items.slice((page - 1) * size, page * size), page, pages, total: items.length };
  }

  /** 仅格式化有限数值，避免把缺失值解释成零。 */
  function formatNumber(value, divisor = 1) {
    return Number.isFinite(value) ? (value / divisor).toFixed(2) : '缺失';
  }

  const api = { filterStocks, paginate, membershipMap, formatNumber, YUAN_PER_YI };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MarketView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
