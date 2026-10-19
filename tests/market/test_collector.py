"""使用官方字段契约验证完整采集与缺失处理。"""

from datetime import datetime, timedelta, timezone

import pytest

from quant_forge.market.collector import INDEX_NAMES, FuyaoCollector
from quant_forge.market.fuyaoClient import FuyaoError

NOW = datetime(2026, 9, 16, 10, tzinfo=timezone(timedelta(hours=8)))
PREVIOUS_MS = int(datetime(2026, 9, 15, tzinfo=NOW.tzinfo).timestamp() * 1000)


class FakeClient:
  """响应夹具保留分页、日历和历史数据语义。"""

  def __init__(self):
    self.calls = []
    self.failMembers = False
    self.duplicate = False

  def get(self, path, params=None):
    params = params or {}
    self.calls.append((path, params))
    if "trading-days" in path:
      data = {"item": [{"date": "20260915"}, {"date": "20260916"}]}
    elif "tickers/list" in path:
      rows = [{"thscode": "600519.SH", "name": "贵州茅台"}, {"thscode": "000001.SZ", "name": "平安银行"}]
      offset = 0 if self.duplicate else params["offset"]
      data = {"item": rows[offset:offset + params["limit"]]}
    elif "historical" in path:
      data = {"item": [{"date_ms": PREVIOUS_MS, "turnover": 123.0}]}
    elif "catalog" in path:
      data = {"item": [{"thscode": params["tag"] + ".TI", "name": "板块"}]}
    elif "constituents" in path:
      if self.failMembers:
        raise FuyaoError("成分采集失败")
      data = {"item": [{"thscode": "600519.SH"}]}
    elif "a-share-index" in path:
      data = {"timestamp": PREVIOUS_MS, "item": [
        {"thscode": code, "price_change_ratio_pct": 1.2} for code in INDEX_NAMES
      ]}
    else:
      rows = [{"thscode": code, "turnover": 999, "last_price": 12, "price_change_ratio_pct": 1}
              for code in ("600519.SH", "000001.SZ")]
      data = {"timestamp": PREVIOUS_MS, "total": 2,
              "item": rows[params["offset"]:params["offset"] + params["limit"]]}
    return {"path": path, "params": params, "response": {"code": 0, "data": data},
            "collectedAt": NOW.isoformat()}


def testBatchHistoryAvoidsPerStockRequests():
  """全市场历史文件覆盖目标日时不再逐股请求，缺失股票明确留空。"""
  class BatchHistory:
    def load(self, tradeDate):
      assert tradeDate == "2026-09-15"
      return {"turnovers": {"600519.SH": 456.0}, "rawResponses": [{"source": "批量历史文件"}]}

  client = FakeClient()
  result = FuyaoCollector(client=client, clock=lambda: NOW, historyLoader=BatchHistory()).collectStocks()
  stocks = {row["code"]: row for row in result["data"]["items"]}
  assert stocks["600519.SH"]["previousTurnover"] == 456.0
  assert stocks["000001.SZ"]["previousTurnover"] is None
  assert not any("historical" in path for path, _ in client.calls)
  assert result["data"]["coverage"]["historySource"] == "全市场历史文件"


def testBatchHistoryFailureFallsBackToValidatedDailyBars():
  """批量文件不可用时可复用原有日 K 路径，并明确记录降级。"""
  class UnavailableHistory:
    def load(self, tradeDate):
      raise FuyaoError("批量历史暂不可用")

  client = FakeClient()
  collector = FuyaoCollector(client=client, clock=lambda: NOW, historyLoader=UnavailableHistory(), sleeper=lambda _: None)
  result = collector.collectStocks()
  assert all(row["previousTurnover"] == 123.0 for row in result["data"]["items"])
  assert result["data"]["coverage"]["historySource"] == "逐股历史日 K（批量来源不可用）"


def testStocksPaginationStaleAndHistoryCache():
  """全市场分页取尽，旧快照不计入今日成交额，昨日成交额按交易日缓存。"""
  client = FakeClient()
  collector = FuyaoCollector(client=client, clock=lambda: NOW, pageSize=1)
  first = collector.collectStocks()
  assert len(first["data"]["items"]) == 2
  assert first["data"]["previousTradeDate"] == "2026-09-15"
  assert all(row["todayTurnover"] is None for row in first["data"]["items"])
  assert all(row["previousTurnover"] == 123 for row in first["data"]["items"])
  assert all(row["status"] == "unknown" for row in first["data"]["items"])
  collector.collectStocks()
  assert sum("historical" in path for path, _ in client.calls) == 2


def testIndicesAndCompleteSectors():
  """指数代码精确匹配，板块成分失败不得返回半批。"""
  client = FakeClient()
  collector = FuyaoCollector(client=client, clock=lambda: NOW)
  assert len(collector.collectIndices()["data"]["items"]) == 4
  assert len(collector.collectSectors()["data"]["items"]) == 2
  client.failMembers = True
  with pytest.raises(FuyaoError, match="成分"):
    collector.collectSectors()


def testDuplicatePageRejected():
  """重复页不能伪装为完整市场。"""
  client = FakeClient()
  client.duplicate = True
  with pytest.raises(FuyaoError, match="重复"):
    FuyaoCollector(client=client, clock=lambda: NOW, pageSize=1).collectStocks()


def testDiskCacheAcrossCollectors(tmp_path):
  """新进程式构造继续复用昨日原始响应，并保留采集时点。"""
  client = FakeClient()
  FuyaoCollector(client=client, clock=lambda: NOW, cacheDir=tmp_path).collectStocks()
  second = FakeClient()
  result = FuyaoCollector(client=second, clock=lambda: NOW, cacheDir=tmp_path).collectStocks()
  assert not any("historical" in path for path, _ in second.calls)
  assert sum("historical" in record["path"] for record in result["rawResponses"]) == 2


def testMissingHistoryRemainsNull():
  """新股昨日无日 K 时保留空值，不凭空补零。"""
  client = FakeClient()
  original = client.get
  def get(path, params=None):
    result = original(path, params)
    if "historical" in path:
      result["response"]["data"]["item"] = []
    return result
  client.get = get
  result = FuyaoCollector(client=client, clock=lambda: NOW).collectStocks()
  assert all(row["previousTurnover"] is None for row in result["data"]["items"])


def testMissingIndexAndBrokenTotalFail():
  """缺失指数和快照提前结束均拒绝发布。"""
  client = FakeClient()
  original = client.get
  def get(path, params=None):
    result = original(path, params)
    if "a-share-index/prices" in path:
      result["response"]["data"]["item"].pop()
    elif "a-share/prices/snapshot" in path:
      result["response"]["data"]["total"] = 9
    return result
  client.get = get
  collector = FuyaoCollector(client=client, clock=lambda: NOW)
  with pytest.raises(FuyaoError, match="不完整"):
    collector.collectIndices()
  with pytest.raises(FuyaoError, match="不完整"):
    collector.collectStocks()


def testInvalidCodeCannotEscapeCache(tmp_path):
  """上游代码不得影响缓存目录范围。"""
  collector = FuyaoCollector(client=FakeClient(), cacheDir=tmp_path)
  with pytest.raises(FuyaoError, match="代码格式"):
    collector.previousTurnover("../escape.SH", "2026-09-15", [])


@pytest.mark.parametrize("content", ["[]", '{"params":null}', '{"params":{"thscode":"600519.SH"}}'])
def testInvalidCacheFallsBack(tmp_path, content):
  """损坏或错误日期缓存必须重新请求而非永久阻断刷新。"""
  folder = tmp_path / "2026-09-15"
  folder.mkdir()
  (folder / "600519.SH.json").write_text(content, encoding="utf-8")
  client = FakeClient()
  collector = FuyaoCollector(client=client, cacheDir=tmp_path, sleeper=lambda _: None)
  assert collector.previousTurnover("600519.SH", "2026-09-15", []) == 123
  assert len(client.calls) == 1
