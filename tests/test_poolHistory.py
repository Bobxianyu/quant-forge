"""历史双池证据的分页、交易日与输入类型校验。"""

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest

POOL = "limit-up-pool"
DAY = date(2026, 9, 15)
DATE_MS = 1789401600000
SIZE = 200


def makePage(total=3, page=1, pool=POOL, pages=None):
  """构造含连续证券代码的完整分页响应。"""
  start = (page - 1) * SIZE
  return {
    "path": f"/api/a-share/special-data/{pool}",
    "params": {"date_ms": DATE_MS, "page": page, "size": SIZE},
    "response": {"code": 0, "data": {
      "timestamp": 0,
      "pagination": {"total": total, "pages": (total + SIZE - 1) // SIZE if pages is None else pages,
                     "page": page, "size": SIZE},
      "item": [{"thscode": f"{index:06d}.SZ"} for index in range(start, min(start + SIZE, total))],
    }},
  }


def checkPage(record, **kwargs):
  """延迟导入，确保实现出现之前先运行失败用例。"""
  from quant_forge.research.poolHistory import validatePoolPage

  return validatePoolPage(record, kwargs.get("pool", POOL), kwargs.get("day", DAY), kwargs.get("expectedPage", 1))


def checkArtifact(records, **kwargs):
  """包装默认调用参数，同时保留外层非字典异常入口。"""
  from quant_forge.research.poolHistory import validatePoolArtifact

  artifact = kwargs.get("artifact", {"records": records})
  return validatePoolArtifact(artifact, kwargs.get("pool", POOL), kwargs.get("day", DAY))


@pytest.mark.parametrize("pool", ["limit-up-pool", "limit-break-pool"])
def testAcceptsCompletePageAndLeavesInputUnchanged(pool):
  """两种池返回结构一致，原始证据不受校验影响。"""
  record = makePage(pool=pool)
  before = deepcopy(record)
  result = checkPage(record, pool=pool)
  assert result == {"rows": record["response"]["data"]["item"], "total": 3, "pages": 1, "size": SIZE}
  assert record == before
  assert result["rows"] is not record["response"]["data"]["item"]


def testCompleteMultiPageArtifactPreservesOrderAndIgnoresCollectionTimestamp():
  """采集时刻不参与交易日判定，完整分页按原顺序合并。"""
  records = [makePage(401, page) for page in range(1, 4)]
  before = deepcopy(records)
  result = checkArtifact(records)
  assert result["total"] == len(result["rows"]) == 401
  assert result["pageCount"] == 3
  assert result["empty"] is False
  assert result["rows"][0]["thscode"] == "000000.SZ"
  assert result["rows"][-1]["thscode"] == "000400.SZ"
  assert records == before


@pytest.mark.parametrize("pages", [0, 1])
def testEmptyPoolRequiresFirstResponseWithoutClaimingNoEvents(pages):
  """空池允许供应商两种页数口径，但仍必须保存第一响应。"""
  result = checkArtifact([makePage(0, pages=pages)])
  assert result == {"rows": [], "total": 0, "pageCount": 1, "empty": True}


def testAllowsMaximumBoundAndExactPageMultiple():
  """八千条四十页是允许的边界，整页末尾不增加空页。"""
  result = checkArtifact([makePage(8000, page) for page in range(1, 41)])
  assert result["total"] == 8000
  assert result["pageCount"] == 40


@pytest.mark.parametrize("value", [None, [], True, "record"])
def testRejectsMalformedRecord(value):
  """外层类型错误统一转换为中文参数错误。"""
  with pytest.raises(ValueError, match="历史池"):
    checkPage(value)


@pytest.mark.parametrize("changes", [
  {"pool": "unknown"}, {"pool": []}, {"pool": None},
  {"day": "2026-09-15"}, {"day": datetime(2026, 9, 15, tzinfo=UTC)}, {"day": None},
  {"expectedPage": True}, {"expectedPage": 0}, {"expectedPage": -1},
  {"expectedPage": 41}, {"expectedPage": "1"}, {"expectedPage": 1.0},
])
def testRejectsInvalidCallerArguments(changes):
  """调用参数不接受隐式类型转换、布尔页码或范围越界。"""
  with pytest.raises(ValueError, match="历史池"):
    checkPage(makePage(), **changes)


@pytest.mark.parametrize("path, value", [
  (("path",), "/api/a-share/special-data/limit-break-pool"),
  (("params",), None), (("params", "date_ms"), DATE_MS + 1),
  (("params", "date_ms"), float(DATE_MS)), (("params", "date_ms"), True),
  (("params", "date_ms"), 10 ** 100), (("params", "page"), True),
  (("params", "page"), 2), (("params", "size"), 100), (("params", "size"), "200"),
  (("response",), []), (("response", "code"), False), (("response", "code"), "0"),
  (("response", "code"), 0.0), (("response", "code"), 1002),
  (("response", "data"), None), (("response", "data", "item"), {}),
  (("response", "data", "item"), None), (("response", "data", "pagination"), []),
])
def testRejectsInvalidRequestOrResponseStructure(path, value):
  """请求身份和响应成功状态都必须有明确、正确类型的证据。"""
  record = makePage()
  target = record
  for key in path[:-1]:
    target = target[key]
  target[path[-1]] = value
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record)


@pytest.mark.parametrize("field", ["total", "pages", "page", "size"])
@pytest.mark.parametrize("value", [True, "1", 1.0, None, -1])
def testRejectsPaginationTypesAndNegativeValues(field, value):
  """分页数字严格是非布尔整数，缺失或负数不能变为空池。"""
  record = makePage()
  record["response"]["data"]["pagination"][field] = value
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record)


@pytest.mark.parametrize("field, value", [
  ("total", 8001), ("total", 0), ("pages", 0), ("pages", 2), ("pages", 41),
  ("page", 0), ("page", 2), ("size", 0), ("size", 199),
])
def testRejectsContradictoryPaginationAndLimits(field, value):
  """声明总量、页数、页号及固定分页大小必须互相匹配。"""
  record = makePage()
  record["response"]["data"]["pagination"][field] = value
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record)


@pytest.mark.parametrize("total, page, count", [(201, 1, 199), (201, 2, 0), (201, 2, 2), (0, 1, 1)])
def testRejectsTruncatedOrExcessRows(total, page, count):
  """每页必须恰好包含该页应有的记录数。"""
  record = makePage(total, page)
  record["response"]["data"]["item"] = [{"thscode": f"{index:06d}.SZ"} for index in range(count)]
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record, expectedPage=page)


@pytest.mark.parametrize("row", [None, [], {}, {"thscode": None}, {"thscode": True},
                                 {"thscode": "600519"}, {"thscode": "600519.sh"},
                                 {"thscode": "６００５１９.SH"}, {"thscode": "600519.SH\n"}])
def testRejectsMissingOrMalformedSecurityCode(row):
  """代码只接受六位 ASCII 数字与沪深北市场后缀。"""
  record = makePage(1)
  record["response"]["data"]["item"] = [row]
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record)


@pytest.mark.parametrize("suffix", ["SH", "SZ", "BJ"])
def testAcceptsSupportedMarketsAndExplicitMatchingDates(suffix):
  """显式交易日存在时检查一致性，不要求供应商必须返回这些字段。"""
  record = makePage(1)
  record["response"]["data"]["item"] = [
    {"thscode": f"123456.{suffix}", "trade_date": DAY.isoformat(), "date_ms": DATE_MS},
  ]
  assert checkPage(record)["total"] == 1


@pytest.mark.parametrize("field, value", [
  ("trade_date", "2026-09-14"), ("trade_date", "20260915"), ("trade_date", None),
  ("trade_date", True), ("date_ms", DATE_MS - 1), ("date_ms", True),
  ("date_ms", float(DATE_MS)), ("date_ms", None),
])
def testRejectsExplicitContradictoryOrMalformedDates(field, value):
  """显式交易日期错误不能被采集时间或正确请求参数遮盖。"""
  record = makePage(1)
  record["response"]["data"]["item"][0][field] = value
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record)


def testRejectsDuplicateCodesWithinPage():
  """同一页代码重复同样破坏证券集合的完整性。"""
  record = makePage(2)
  record["response"]["data"]["item"][1] = {"thscode": "000000.SZ"}
  with pytest.raises(ValueError, match="重复"):
    checkPage(record)


def testRejectsDuplicateCodesAcrossPages():
  """分页重叠不能在总记录数正确时悄悄通过。"""
  records = [makePage(201, page) for page in (1, 2)]
  records[1]["response"]["data"]["item"][0] = {"thscode": "000000.SZ"}
  with pytest.raises(ValueError, match="重复"):
    checkArtifact(records)


@pytest.mark.parametrize("records", [[], None, {}, [makePage(201)], [makePage(201, 2), makePage(201)],
                                     [makePage(), makePage()], [makePage(201), makePage(202, 2)]])
def testRejectsIncompleteUnorderedOrChangingPageSeries(records):
  """缺页、反序、重复页及查询过程中总数变化均需要重核证据。"""
  with pytest.raises(ValueError, match="历史池"):
    checkArtifact(records)


@pytest.mark.parametrize("artifact", [None, [], True, {}])
def testRejectsMalformedArtifact(artifact):
  """证据文件外层必须含有效 records 数组。"""
  with pytest.raises(ValueError, match="历史池"):
    checkArtifact([], artifact=artifact)


def testShanghaiHistoricalSummerTimeUsesActualLocalMidnight():
  """历史夏令时的上海零点不能按固定八小时时差校验。"""
  record = makePage(1)
  record["params"]["date_ms"] = 673110000000
  assert checkPage(record, day=date(1991, 5, 2))["total"] == 1
  record["params"]["date_ms"] = 673113600000
  with pytest.raises(ValueError, match="历史池"):
    checkPage(record, day=date(1991, 5, 2))
