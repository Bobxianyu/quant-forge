"""采集完整市场批次，保留数据时点并拒绝用旧行情充当今日成交额。"""

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fuyaoClient import FuyaoClient, FuyaoError

SHANGHAI = timezone(timedelta(hours=8))
PAGE_SIZE = 100
MAX_PAGES = 1000
HISTORY_DELAY_SECONDS = 0.1
STOCK_CODE_PATTERN = re.compile(r"\d{6}\.(?:SH|SZ)")
MILLISECONDS = 1000
INDEX_NAMES = {"883994.TI": "昨日打首板表现", "883997.TI": "昨日非ST二板表现",
               "883410.TI": "最近多板", "883900.TI": "昨日涨停表现"}


def quoteTime(value):
  """将上游毫秒时间转换为上海时间，缺失或非法值保留为空。"""
  try:
    return datetime.fromtimestamp(value / MILLISECONDS, SHANGHAI).isoformat() if value is not None else None
  except (TypeError, ValueError, OverflowError, OSError):
    return None


def finiteNumber(value):
  """非有限值和非数值不作为真实市场数值。"""
  return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


class FuyaoCollector:
  """单模块全量成功后才返回，历史日 K 可跨进程恢复。"""

  def __init__(self, client=None, clock=None, pageSize=PAGE_SIZE, cacheDir=None, sleeper=time.sleep, historyLoader=None):
    """配置客户端、时钟及历史缓存；默认顺序限速访问避免集中请求。"""
    if not isinstance(pageSize, int) or not 1 <= pageSize <= 10000:
      raise FuyaoError("采集器：分页大小必须在 1 至 10000 之间")
    self.client = client or FuyaoClient()
    self.clock = clock or (lambda: datetime.now(SHANGHAI))
    self.pageSize = pageSize
    self.cacheDir = Path(cacheDir) if cacheDir else None
    self.sleeper = sleeper
    self.historyCache = {}
    self.historyLoader = historyLoader

  def request(self, path, params, evidence):
    """记录原始证据并校验列表结构。"""
    record = self.client.get(path, params)
    data = record["response"]["data"]
    if not isinstance(data.get("item"), list) or any(not isinstance(row, dict) for row in data["item"]):
      raise FuyaoError("采集器：接口数据列表结构错误")
    evidence.append(record)
    return data

  def result(self, data, evidence):
    """生成统一模块批次返回值。"""
    return {"data": data, "rawResponses": evidence, "collectedAt": self.clock().isoformat(),
            "source": "同花顺 Fuyao"}

  def collectIndices(self):
    """精确读取四项已验证的特色指数。"""
    evidence = []
    data = self.request("/api/a-share-index/prices/snapshot", {"thscodes": ",".join(INDEX_NAMES)}, evidence)
    rows = {row.get("thscode"): row for row in data["item"]}
    if set(rows) != set(INDEX_NAMES) or len(data["item"]) != len(INDEX_NAMES):
      raise FuyaoError("指数采集：四项指数响应不完整或重复")
    items = [{"code": code, "name": name, "valuePct": finiteNumber(rows[code].get("price_change_ratio_pct")),
              "quoteAt": quoteTime(data.get("timestamp"))} for code, name in INDEX_NAMES.items()]
    return self.result({"items": items}, evidence)

  def collectPages(self, path, params, evidence, requireTotal=False):
    """分页取尽，拒绝重复代码、总数变化及提前空页。"""
    result = {}
    total = None
    for page in range(MAX_PAGES):
      data = self.request(path, {**params, "limit": self.pageSize, "offset": page * self.pageSize}, evidence)
      if requireTotal:
        currentTotal = data.get("total")
        if not isinstance(currentTotal, int) or currentTotal < 0 or (total is not None and total != currentTotal):
          raise FuyaoError("市场采集：分页总数缺失或发生变化")
        total = currentTotal
      for row in data["item"]:
        code = row.get("thscode")
        if not isinstance(code, str) or code in result:
          raise FuyaoError("市场采集：分页代码缺失或重复")
        result[code] = {**row, "quoteAt": quoteTime(data.get("timestamp"))}
      if total is not None and len(result) >= total:
        if len(result) != total:
          raise FuyaoError("市场采集：分页数量超过总数")
        return result
      if len(data["item"]) < self.pageSize:
        if total is not None:
          raise FuyaoError("市场采集：分页提前结束，结果不完整")
        return result
    raise FuyaoError("市场采集：超过分页安全上限")

  def previousTurnover(self, code, tradeDate, evidence):
    """实际采集昨日未复权日 K；缓存完整响应，空数据允许后续重取。"""
    if not isinstance(code, str) or not STOCK_CODE_PATTERN.fullmatch(code):
      raise FuyaoError("历史采集：股票代码格式非法")
    try:
      start = datetime.strptime(tradeDate, "%Y-%m-%d").replace(tzinfo=SHANGHAI)
      if start.date().isoformat() != tradeDate:
        raise ValueError
    except (TypeError, ValueError):
      raise FuyaoError("历史采集：交易日期格式非法") from None
    params = {"thscode": code, "interval": "1d", "adjust": "none",
              "start": int(start.timestamp() * MILLISECONDS),
              "end": int((start + timedelta(days=1)).timestamp() * MILLISECONDS) - 1}
    cacheKey = (code, tradeDate)
    cachePath = self.cacheDir / tradeDate / f"{code}.json" if self.cacheDir else None
    record = self.historyCache.get(cacheKey)
    if record is None and cachePath and cachePath.exists():
      try:
        candidate = json.loads(cachePath.read_text(encoding="utf-8"))
        rows = candidate["response"]["data"]["item"]
        validRows = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        if (candidate.get("params") == params and candidate["response"].get("code") == 0
            and candidate.get("path") == "/api/a-share/prices/historical" and validRows
            and isinstance(candidate.get("collectedAt"), str)):
          record = candidate
      except (OSError, ValueError, KeyError, TypeError, AttributeError):
        record = None
    if record is None:
      self.sleeper(HISTORY_DELAY_SECONDS)
      self.request("/api/a-share/prices/historical", params, evidence)
      record = evidence[-1]
    else:
      evidence.append(record)
    try:
      rows = record["response"]["data"]["item"]
      values = [finiteNumber(row.get("turnover")) for row in rows
                if (quoteTime(row.get("date_ms")) or "")[:10] == tradeDate]
    except (KeyError, TypeError, AttributeError):
      raise FuyaoError("历史采集：缓存响应结构错误") from None
    if len(values) > 1:
      raise FuyaoError("历史采集：同一交易日出现重复日 K")
    value = values[0] if values else None
    if value is not None:
      self.historyCache[cacheKey] = record
      if cachePath:
        try:
          cachePath.parent.mkdir(parents=True, exist_ok=True)
          temporary = cachePath.with_suffix(".tmp")
          temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8", newline="\n")
          temporary.replace(cachePath)
        except OSError:
          raise FuyaoError("历史采集：昨日成交额缓存写入失败") from None
    return value

  def collectStocks(self):
    """全量采集沪深 A 股，昨日历史数据全部请求成功后统一发布。"""
    evidence = []
    today = self.clock().astimezone(SHANGHAI).date().isoformat()
    calendar = self.request("/api/a-share/calendar/trading-days", {}, evidence)
    try:
      dates = sorted({datetime.strptime(row["date"], "%Y%m%d").replace(tzinfo=SHANGHAI).date().isoformat()
                      for row in calendar["item"]})
    except (KeyError, TypeError, ValueError):
      raise FuyaoError("股票采集：交易日历格式错误") from None
    previousDates = [day for day in dates if day < today]
    if not previousDates:
      raise FuyaoError("股票采集：交易日历缺少前一交易日")
    previousDate = previousDates[-1]
    catalog = self.collectPages("/api/meta/tickers/list", {"asset_type": "a-share"}, evidence)
    stocks = {code: row for code, row in catalog.items() if code.endswith((".SH", ".SZ"))}
    if not stocks:
      raise FuyaoError("股票采集：沪深 A 股目录为空")
    historical, historySource = self.loadHistoryBatch(stocks, previousDate, evidence)
    # 历史准备完成后再获取最新行情，避免将长时间采集开始时的价格当作最新批次。
    quotes = self.collectPages("/api/a-share/prices/snapshot", {}, evidence, requireTotal=True)
    items = []
    for code, row in stocks.items():
      quote = quotes.get(code, {})
      quoteAt = quote.get("quoteAt")
      current = bool(quoteAt and quoteAt[:10] == today and today in dates)
      items.append({"code": code, "name": row.get("name"), "lastPrice": finiteNumber(quote.get("last_price")),
                    "returnPct": finiteNumber(quote.get("price_change_ratio_pct")),
                    "todayTurnover": finiteNumber(quote.get("turnover")) if current else None,
                    "previousTurnover": historical.get(code),
                    "quoteAt": quoteAt, "tradeDate": today, "status": "unknown"})
    received = sum(code in quotes for code in stocks)
    coverage = {"expected": len(stocks), "received": received, "complete": received == len(stocks),
                "missingQuotes": sorted(set(stocks) - set(quotes)),
                "currentTurnover": sum(row["todayTurnover"] is not None for row in items),
                "previousTurnover": sum(row["previousTurnover"] is not None for row in items),
                "historySource": historySource,
                "quoteTimeBasis": "上游快照页最新时间，非逐股成交时间", "statusKnown": False}
    return self.result({"items": items, "previousTradeDate": previousDate, "tradeDate": today,
                        "coverage": coverage}, evidence)

  def loadHistoryBatch(self, stocks, previousDate, evidence):
    """优先使用经过日期校验的全市场文件，不可用时回退至已验证的逐股日 K。"""
    historySource = "逐股历史日 K"
    if self.historyLoader is not None:
      try:
        batch = self.historyLoader.load(previousDate)
        evidence.extend(batch["rawResponses"])
        return batch["turnovers"], "全市场历史文件"
      except FuyaoError as error:
        historySource = "逐股历史日 K（批量来源不可用）"
        evidence.append({"source": "批量历史降级", "reason": str(error), "collectedAt": self.clock().isoformat()})
    historical = {code: self.previousTurnover(code, previousDate, evidence) for code in stocks}
    return historical, historySource

  def collectSectors(self):
    """全取行业、概念目录与每个板块当前成分，失败时不返回半批。"""
    evidence = []
    items = []
    seen = set()
    for tag, sectorType in (("industry", "industry"), ("cn_concept", "concept")):
      catalog = self.request("/api/a-share-index/catalog/ths-index-list", {"tag": tag}, evidence)
      if not catalog["item"]:
        raise FuyaoError("板块采集：板块目录为空")
      for row in catalog["item"]:
        code = row.get("thscode")
        if not isinstance(code, str) or code in seen:
          raise FuyaoError("板块采集：目录代码缺失或重复")
        seen.add(code)
        self.sleeper(HISTORY_DELAY_SECONDS)
        members = self.request("/api/a-share-index/constituents/ths-stock-list", {"thscode": code}, evidence)
        codes = [member.get("thscode") for member in members["item"]]
        if any(not isinstance(member, str) for member in codes) or len(codes) != len(set(codes)):
          raise FuyaoError("板块采集：成分代码缺失或重复")
        items.append({"code": code, "name": row.get("name"), "type": sectorType, "members": codes})
    return self.result({"items": items, "membershipAsOf": self.clock().isoformat()}, evidence)
