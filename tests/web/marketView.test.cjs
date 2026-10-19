const test = require('node:test');
const assert = require('node:assert/strict');
const {
  filterStocks, paginate, membershipMap, formatNumber,
} = require('../../src/quant_forge/web/static/marketView.js');

const stocks = [
  { code: '600001', name: '股票甲', previousTurnover: 2e8, todayTurnover: 0 },
  { code: '000001', name: '股票乙', previousTurnover: 8e8, todayTurnover: 3e8 },
  { code: '300001', name: '股票丙', previousTurnover: null, todayTurnover: null },
];

// 默认不过滤任何股票，缺失成交额排在最后。
test('默认昨日成交额降序，保留零值与缺失数据', () => {
  assert.deepEqual(filterStocks(stocks).map((stock) => stock.code), ['000001', '600001', '300001']);
  assert.equal(stocks[0].code, '600001');
});

test('查询、成交额和多归属板块过滤可组合', () => {
  const memberships = membershipMap([
    { code: 'a', name: '行业甲', type: 'industry', members: ['600001'] },
    { code: 'b', name: '概念甲', type: 'concept', members: ['600001', '000001'] },
  ]);
  assert.equal(memberships.get('600001').length, 2);
  assert.equal(filterStocks(stocks, { query: '股票甲', sector: 'concept:b', minimum: 1 }, memberships).length, 1);
  assert.equal(filterStocks(stocks, { minimum: 3 }).length, 1);
  assert.equal(filterStocks(stocks, { query: '不存在' }).length, 0);
});

test('升序仍把缺失值排最后，非法条件采用默认值', () => {
  assert.equal(filterStocks(stocks, { direction: 'asc' }).at(-1).code, '300001');
  assert.equal(filterStocks(stocks, { minimum: -1 }).length, 3);
  assert.equal(filterStocks(stocks, { minimum: '非法' }).length, 3);
});

test('分页能访问全部股票，越界页会纠正', () => {
  assert.deepEqual(paginate(stocks, 99, 2), { items: [stocks[2]], page: 2, pages: 2, total: 3 });
  assert.deepEqual(paginate([], 1, 50), { items: [], page: 1, pages: 1, total: 0 });
});

test('格式化严格区分缺失与真实零值', () => {
  assert.equal(formatNumber(null), '缺失');
  assert.equal(formatNumber(undefined), '缺失');
  assert.equal(formatNumber(NaN), '缺失');
  assert.equal(formatNumber(0), '0.00');
});
