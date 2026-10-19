"""全市场文件离线审计的边界、分批统计和报告语义测试。"""

import json
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_forge.research import fullDumpAudit

TRADE_DATE = date(2026, 9, 16)
DAY_MILLISECONDS = 86_400_000
SHANGHAI_OFFSET_MILLISECONDS = 28_800_000


@pytest.fixture
def auditor():
  """直接加载受版本管理的研究模块，不依赖被忽略的输出目录。"""
  return fullDumpAudit


def dateMillis(day=TRADE_DATE):
  """构造精确的上海零点毫秒时间戳。"""
  return (day - date(1970, 1, 1)).days * DAY_MILLISECONDS - SHANGHAI_OFFSET_MILLISECONDS


def dailyRow(**changes):
  """生成一条正常未复权日 K，允许覆盖边界字段。"""
  row = {
    "thscode": "000001.SZ", "currency": "CNY", "interval": "1d", "adjusted": "none",
    "date_ms": dateMillis(), "open_price": 10.0, "high_price": 12.0, "low_price": 9.0,
    "close_price": 11.0, "volume": 1000.0, "turnover": 11000.0,
  }
  return row | changes


def eventRow(**changes):
  """生成原始复权事件，不将它当成现成复权系数。"""
  row = {
    "thscode": "000001.SZ", "ticker": "000001", "currency": "CNY", "ex_date_ms": dateMillis(),
    "dividend_per_share": 0.1, "per_share_bonus": 0.0, "allotment_ratio": 0.0, "allotment_price": 0.0,
  }
  return row | changes


def writeInputs(tmpPath, dailyRows, eventRows=None):
  """写小型真实 Parquet，跨行组验证累计行为。"""
  daily = tmpPath / "daily.parquet"
  events = tmpPath / "events.parquet"
  pq.write_table(pa.Table.from_pylist(dailyRows), daily, row_group_size=2)
  pq.write_table(pa.Table.from_pylist(eventRows or [eventRow()]), events, row_group_size=2)
  return daily, events


def testValidFilesCountActualDatesCodesAndFutureAnnouncements(auditor, tmp_path):
  """历史与未来事件分别统计，未来预告不被自动判错。"""
  later = TRADE_DATE + timedelta(days=1)
  daily, events = writeInputs(tmp_path, [dailyRow(), dailyRow(thscode="600519.SH")],
                              [eventRow(), eventRow(ex_date_ms=dateMillis(later))])
  report = auditor.auditDumps(daily, events, TRADE_DATE)
  assert report["dailyK"]["actualRows"] == 2
  assert report["dailyK"]["securityCount"] == 2
  assert report["dailyK"]["rowsByDate"] == {TRADE_DATE.isoformat(): 2}
  assert report["dailyK"]["uniqueKeysVerified"] is True
  assert report["dailyK"]["rowCountMatchesMetadata"] is True
  assert report["adjustments"]["effectiveOnOrBeforeAsOfRows"] == 1
  assert report["adjustments"]["futureEffectiveRows"] == 1
  assert report["adjustments"]["invalidNumericRows"] == 0
  assert any("未来" in item and "预告" in item for item in report["limitations"])
  assert any("权重" in item for item in report["limitations"])
  json.dumps(report, allow_nan=False)


def testDailyAnomaliesRemainExplicitAndJsonSafe(auditor, tmp_path):
  """非法代码、口径、价格、量额和非上海零点分别计数，不补成零。"""
  daily, events = writeInputs(tmp_path, [
    dailyRow(), dailyRow(thscode="BAD"), dailyRow(currency="USD", adjusted="forward"),
    dailyRow(high_price=float("nan")), dailyRow(low_price=12.0), dailyRow(volume=-1.0),
    dailyRow(date_ms=dateMillis() + 1),
  ])
  report = auditor.auditDumps(daily, events, TRADE_DATE)
  data = report["dailyK"]
  assert data["actualRows"] == 7
  assert data["invalidCodeRows"] == 1
  assert data["nonStandardBasisRows"] == 1
  assert data["invalidOhlcRows"] == 2
  assert data["invalidVolumeOrTurnoverRows"] == 1
  assert data["invalidDateRows"] == 1
  assert data["rowsByDate"] == {TRADE_DATE.isoformat(): 6}
  assert data["uniqueKeysVerified"] is False
  json.dumps(report, allow_nan=False)


def testAdjacentDuplicateAcrossBatchesIsCounted(auditor, tmp_path, monkeypatch):
  """已验证全局非降序时，相邻去重统计跨批次仍连续。"""
  monkeypatch.setattr(auditor, "BATCH_ROWS", 2)
  later = dateMillis(TRADE_DATE + timedelta(days=1))
  daily, events = writeInputs(tmp_path, [dailyRow(), dailyRow(date_ms=later), dailyRow(date_ms=later)])
  data = auditor.auditDumps(daily, events, TRADE_DATE)["dailyK"]
  assert data["globalKeyOrderVerified"] is True
  assert data["duplicateCheckComplete"] is True
  assert data["duplicateRowsBeyondFirst"] == 1
  assert data["uniqueKeysVerified"] is False
  assert data["futureDatedRows"] == 2


def testUnsortedDataCannotClaimCompleteDuplicateCheck(auditor, tmp_path):
  """非相邻重复藏在乱序数据中时，报告未知而不是误报零重复。"""
  daily, events = writeInputs(tmp_path, [dailyRow(), dailyRow(thscode="600519.SH"), dailyRow()])
  data = auditor.auditDumps(daily, events, TRADE_DATE)["dailyK"]
  assert data["keyOrderViolationRows"] == 1
  assert data["adjacentDuplicateRowsObserved"] == 0
  assert data["duplicateCheckComplete"] is False
  assert data["duplicateRowsBeyondFirst"] is None
  assert data["uniqueKeysVerified"] is False


@pytest.mark.parametrize("volume, turnover", [(0.0, 11000.0), (1000.0, 0.0), (0.0, 0.0)])
def testZeroVolumeOrTurnoverIsObservedWithoutInferringSuspension(auditor, tmp_path, volume, turnover):
  """零量或零额单独按行计数，不混入负数、空值及非有限量额异常。"""
  daily, events = writeInputs(tmp_path, [dailyRow(volume=volume, turnover=turnover)])
  data = auditor.auditDumps(daily, events, TRADE_DATE)["dailyK"]
  assert data["zeroVolumeOrTurnoverRows"] == 1
  assert data["invalidVolumeOrTurnoverRows"] == 0
  assert data["invalidOhlcRows"] == 0


def testAdjustmentDuplicateGroupsAndInvalidValues(auditor, tmp_path):
  """重复生效日组是待核验现象，数值和日期缺陷独立报告。"""
  daily, events = writeInputs(tmp_path, [dailyRow()], [
    eventRow(), eventRow(), eventRow(dividend_per_share=-1.0, allotment_price=float("inf")),
    eventRow(ex_date_ms=dateMillis() + 1), eventRow(thscode="BAD", ticker="BAD", currency="USD"),
  ])
  report = auditor.auditDumps(daily, events, TRADE_DATE)
  data = report["adjustments"]
  assert data["duplicateEffectiveDateGroupCount"] is None
  assert data["duplicateRowsBeyondFirst"] is None
  assert data["duplicateGroupCountLowerBound"] == 1
  assert data["duplicateRowsBeyondFirstLowerBound"] == 2
  assert data["duplicateCheckComplete"] is False
  assert data["invalidNumericRows"] == 1
  assert data["invalidNumericFieldCounts"]["dividend_per_share"] == 1
  assert data["invalidNumericFieldCounts"]["allotment_price"] == 1
  assert data["invalidDateRows"] == 1
  assert data["invalidCodeRows"] == 1
  assert data["invalidTickerRows"] == 1
  assert data["nonCnyRows"] == 1
  assert any("重复" in item and "非法" in item for item in report["limitations"])
  json.dumps(report, allow_nan=False)


def testAdjustmentDuplicateGroupsAreCompleteEvenWhenUnsorted(auditor, tmp_path):
  """有效事件键未超上限时，跨非相邻记录的重复组也可完整核验。"""
  daily, events = writeInputs(tmp_path, [dailyRow()], [
    eventRow(), eventRow(thscode="600519.SH", ticker="600519"), eventRow(),
  ])
  data = auditor.auditDumps(daily, events, TRADE_DATE)["adjustments"]
  assert data["duplicateEffectiveDateGroupCount"] == 1
  assert data["duplicateRowsBeyondFirst"] == 1
  assert data["duplicateCheckComplete"] is True


def testTrackingAndExamplesHaveHardCaps(auditor, tmp_path, monkeypatch):
  """元数据集合达到上限后标明不完整，不能伪造精确数量。"""
  monkeypatch.setattr(auditor, "MAX_TRACKED_CODES", 1)
  monkeypatch.setattr(auditor, "MAX_TRACKED_DATES", 1)
  monkeypatch.setattr(auditor, "MAX_TRACKED_EVENT_KEYS", 1)
  monkeypatch.setattr(auditor, "MAX_EXAMPLES", 1)
  nextDate = dateMillis(TRADE_DATE + timedelta(days=1))
  daily, events = writeInputs(tmp_path, [dailyRow(high_price=0.0),
    dailyRow(thscode="600519.SH", date_ms=nextDate, high_price=0.0)],
    [eventRow(), eventRow(ex_date_ms=nextDate), eventRow(ex_date_ms=nextDate)])
  report = auditor.auditDumps(daily, events, TRADE_DATE)
  assert report["dailyK"]["securityCount"] is None
  assert report["dailyK"]["securityCountComplete"] is False
  assert report["dailyK"]["rowsByDateComplete"] is False
  assert len(report["dailyK"]["samples"]["invalidOhlcRows"]) == 1
  assert report["adjustments"]["duplicateCheckComplete"] is False
  assert report["adjustments"]["duplicateEffectiveDateGroupCount"] is None


def testMissingSchemaFieldFailsInChinese(auditor, tmp_path):
  """缺字段不能悄悄降级为成功审计。"""
  row = dailyRow()
  del row["close_price"]
  daily, events = writeInputs(tmp_path, [row])
  with pytest.raises(ValueError, match="缺少必要字段.*close_price"):
    auditor.auditDumps(daily, events, TRADE_DATE)


def testCliWritesStrictUtf8JsonAndProtectsSource(auditor, tmp_path, capsys):
  """命令行产物可严格解码，并拒绝输出路径覆盖任一源文件。"""
  daily, events = writeInputs(tmp_path, [dailyRow()])
  output = tmp_path / "report.json"
  arguments = ["--daily-k", str(daily), "--adjustments", str(events), "--as-of", "2026-09-16"]
  assert auditor.main([*arguments, "--output", str(output)]) == 0
  raw = output.read_bytes()
  assert raw.endswith(b"\n") and b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
  assert json.loads(raw)["dailyK"]["actualRows"] == 1
  before = daily.read_bytes()
  assert auditor.main([*arguments, "--output", str(daily)]) == 2
  assert daily.read_bytes() == before
  assert "覆盖" in capsys.readouterr().err


@pytest.mark.parametrize("asOf", ["2026-02-30", "2026-9-16"])
def testCliRejectsInvalidAsOfDate(auditor, tmp_path, asOf, capsys):
  """判断事件时点的日期必须严格有效，不能静默修正。"""
  daily, events = writeInputs(tmp_path, [dailyRow()])
  assert auditor.main(["--daily-k", str(daily), "--adjustments", str(events),
                       "--as-of", asOf, "--output", str(tmp_path / "report.json")]) == 2
  assert "日期" in capsys.readouterr().err


@pytest.mark.parametrize("timestamp, expected", [
  (673110000000, date(1991, 5, 2)),
  (675702000000, date(1991, 6, 1)),
  (676306800000, date(1991, 6, 8)),
  (683132400000, date(1991, 8, 26)),
])
def testHistoricalShanghaiSummerMidnightIsValid(auditor, timestamp, expected):
  """真实历史事件在上海夏令时为零点，固定 UTC+8 会错误地落到前一日 23 点。"""
  assert auditor.tradeDate(timestamp) == expected


def testFixedOffsetMidnightCannotReplaceHistoricalLocalMidnight(auditor):
  """夏令时期间固定 UTC+8 的零点实际为当地一点，不能被零点校验放过。"""
  assert auditor.tradeDate(673113600000) is None
