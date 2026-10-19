"""分批核验本地全市场日 K 与原始复权事件，不请求网络或计算策略权重。"""

# 项目研究工具模块采用小驼峰文件名。
# ruff: noqa: N999

import argparse
import json
import math
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

BATCH_ROWS = 8192
MAX_TRACKED_CODES = 100_000
MAX_TRACKED_DATES = 20_000
MAX_TRACKED_EVENT_KEYS = 100_000
MAX_EXAMPLES = 5
MAX_SAMPLE_TEXT_LENGTH = 160
EPOCH_TIME = datetime(1970, 1, 1, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")
CODE_PATTERN = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")
TICKER_PATTERN = re.compile(r"[0-9]{6}")
OHLC_FIELDS = ("open_price", "high_price", "low_price", "close_price")
EVENT_NUMERIC_FIELDS = ("dividend_per_share", "per_share_bonus", "allotment_ratio", "allotment_price")
DAILY_COLUMNS = (
  "thscode", "currency", "interval", "adjusted", "date_ms", *OHLC_FIELDS, "volume", "turnover",
)
EVENT_COLUMNS = ("thscode", "ticker", "ex_date_ms", *EVENT_NUMERIC_FIELDS, "currency")
COMMON_COUNTERS = ("invalidCodeRows", "invalidDateRows")
DAILY_COUNTERS = (
  "nonStandardBasisRows", "invalidOhlcRows", "invalidVolumeOrTurnoverRows", "futureDatedRows",
  "zeroVolumeOrTurnoverRows", "invalidKeyRows", "keyOrderViolationRows", "adjacentDuplicateRowsObserved",
)
EVENT_COUNTERS = (
  "invalidTickerRows", "nonCnyRows", "invalidNumericRows", "invalidKeyRows",
  "effectiveOnOrBeforeAsOfRows", "futureEffectiveRows",
)
LIMITATIONS = [
  "这是离线文件质量统计，不证明历史证券全集完整，也不证明每天每只证券都有记录。",
  "缺少历史 ST、停牌、退市及板块成分信息，不能用当前状态或当前成分回填历史。",
  "复权文件包含原始事件，不是可以直接乘价格的复权系数。",
  "未来生效事件可能是预告，不一概视为错误，也不能提前回填历史收益；生效日期不等于公告日期。",
  "重复代码与生效日组可能包含不同事件，不自行断言全部非法；需要核对原始事件语义。",
  "日 K 只有全局有效键非降序检查通过时，才以相邻记录完成去重核验；乱序时不会声称已全量去重。",
  "逐日行数包括重复记录和其他字段异常记录，只要求日期有效；证券数仅统计格式有效的代码。",
  "价格零值、无效量额等仅作为待核验现象保留，不补值、不自动清洗，也不直接宣称不可交易。",
  "零成交量或零成交额单独计数，不等同于数值异常，也不能据此推断停牌。",
  "统计集合达到硬上限时标明不完整，精确数量返回 null；日期最值仍覆盖所有可解释的有效日期。",
  "本报告不能证明可以选择最优权重，不是候选有效性验证或可成交账户回撤回测。",
]


class AuditArgumentParser(argparse.ArgumentParser):
  """将命令行解析失败转换为统一的中文错误。"""

  def error(self, message):
    """保留明确的参数范围，避免默认英文异常替代中文提示。"""
    raise ValueError("审计参数错误：请提供 --daily-k、--adjustments、--as-of、--output，并检查未知参数")


def isFiniteNumber(value):
  """只有非布尔的有限实数才可参与价格或量额检查。"""
  if not isinstance(value, (int, float)) or isinstance(value, bool):
    return False
  try:
    return math.isfinite(value)
  except OverflowError:
    return False


def tradeDate(timestamp):
  """按真实上海历史时区解析整数毫秒，拒绝越界和非当地零点。"""
  if not isinstance(timestamp, int) or isinstance(timestamp, bool):
    return None
  try:
    # 整数毫秒先定位 UTC 时刻，再应用历史夏令时，避免固定时差把合法事件移到前一天。
    localTime = (EPOCH_TIME + timedelta(milliseconds=timestamp)).astimezone(SHANGHAI)
  except OverflowError:
    return None
  if any((localTime.hour, localTime.minute, localTime.second, localTime.microsecond)):
    return None
  return localTime.date()


def safeValue(value):
  """限制样例字符串长度，将非有限数转换成文本，保证严格 JSON 可序列化。"""
  if value is None or isinstance(value, bool):
    return value
  if isFiniteNumber(value):
    return value
  return str(value)[:MAX_SAMPLE_TEXT_LENGTH]


class AuditTracker:
  """只保存受限的代码、日期统计和少量样例，不保存全部数据行或日 K 键。"""

  def __init__(self, path, metadata, counters):
    """初始化文件计数及硬上限受控的辅助集合。"""
    self.result = {
      "path": str(Path(path).resolve()), "fileBytes": Path(path).stat().st_size,
      "declaredRows": metadata.num_rows, "actualRows": 0,
      "dateMin": None, "dateMax": None, "samples": {},
      **dict.fromkeys((*COMMON_COUNTERS, *counters), 0),
    }
    self.codes = set()
    self.codesComplete = True
    self.dateCounts = {}
    self.datesComplete = True

  def issue(self, field, row):
    """累计异常或观察现象的行数，每种现象只保留固定数量的字段样例。"""
    self.result[field] += 1
    examples = self.result["samples"].setdefault(field, [])
    if len(examples) < MAX_EXAMPLES:
      examples.append({"rowNumber": self.result["actualRows"],
                       "values": {key: safeValue(value) for key, value in row.items()}})

  def observe(self, row, dateField):
    """记录单行日期与证券覆盖，返回可核验的代码和日期。"""
    self.result["actualRows"] += 1
    code = row["thscode"]
    if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
      self.issue("invalidCodeRows", row)
      code = None
    elif code not in self.codes:
      if len(self.codes) < MAX_TRACKED_CODES:
        self.codes.add(code)
      else:
        self.codesComplete = False
    day = tradeDate(row[dateField])
    if day is None:
      self.issue("invalidDateRows", row)
    else:
      dayText = day.isoformat()
      self.result["dateMin"] = min(self.result["dateMin"] or dayText, dayText)
      self.result["dateMax"] = max(self.result["dateMax"] or dayText, dayText)
      if dayText in self.dateCounts:
        self.dateCounts[dayText] += 1
      elif len(self.dateCounts) < MAX_TRACKED_DATES:
        self.dateCounts[dayText] = 1
      else:
        self.datesComplete = False
    return code, day

  def finish(self):
    """输出明确的完整性标记，不把受限集合大小当作精确证券数量。"""
    self.result.update({
      "rowCountMatchesMetadata": self.result["actualRows"] == self.result["declaredRows"],
      "securityCount": len(self.codes) if self.codesComplete else None,
      "securityCountComplete": self.codesComplete,
      "trackedSecurityCountLowerBound": len(self.codes),
      "rowsByDate": dict(sorted(self.dateCounts.items())),
      "rowsByDateComplete": self.datesComplete,
    })
    return self.result


def openParquet(path, columns):
  """只打开元数据并检查字段契约，调用方负责关闭流式读取器。"""
  reader = pq.ParquetFile(path)
  missing = set(columns) - set(reader.schema_arrow.names)
  if missing:
    reader.close()
    raise ValueError(f"文件 {path} 缺少必要字段：{', '.join(sorted(missing))}")
  if len(reader.schema_arrow.names) != len(set(reader.schema_arrow.names)):
    reader.close()
    raise ValueError(f"文件 {path} 存在重复列名，不能可靠读取")
  return reader


def validOhlc(row):
  """检查正价格及低价不高于开收、高价不低于开收的基本关系。"""
  if any(not isFiniteNumber(row[field]) or row[field] <= 0 for field in OHLC_FIELDS):
    return False
  return (row["low_price"] <= min(row["open_price"], row["close_price"])
          and row["high_price"] >= max(row["open_price"], row["close_price"]))


def auditDailyK(path, asOf):
  """流式核验日 K；只有全局键顺序已证实时才给出完整重复统计。"""
  reader = openParquet(path, DAILY_COLUMNS)
  tracker = AuditTracker(path, reader.metadata, DAILY_COUNTERS)
  previousKey = None
  try:
    for batch in reader.iter_batches(batch_size=BATCH_ROWS, columns=list(DAILY_COLUMNS), use_threads=False):
      # 仅对当前批次展开 Python 对象，内存不随日 K 总行数增长。
      for row in batch.to_pylist():
        code, day = tracker.observe(row, "date_ms")
        if (row["currency"], row["interval"], row["adjusted"]) != ("CNY", "1d", "none"):
          tracker.issue("nonStandardBasisRows", row)
        if not validOhlc(row):
          tracker.issue("invalidOhlcRows", row)
        if any(not isFiniteNumber(row[field]) or row[field] < 0 for field in ("volume", "turnover")):
          tracker.issue("invalidVolumeOrTurnoverRows", row)
        if any(isFiniteNumber(row[field]) and row[field] == 0 for field in ("volume", "turnover")):
          tracker.issue("zeroVolumeOrTurnoverRows", row)
        if day is not None and day > asOf:
          tracker.issue("futureDatedRows", row)
        if code is None or day is None:
          tracker.issue("invalidKeyRows", row)
          previousKey = None
          continue
        key = (code, row["date_ms"])
        if previousKey is not None:
          if key < previousKey:
            tracker.issue("keyOrderViolationRows", row)
          elif key == previousKey:
            tracker.issue("adjacentDuplicateRowsObserved", row)
        previousKey = key
  finally:
    reader.close()
  result = tracker.finish()
  ordered = result["keyOrderViolationRows"] == 0 and result["invalidKeyRows"] == 0
  result.update({
    "keyDefinition": ["thscode", "date_ms"], "globalKeyOrderVerified": ordered,
    "duplicateCheckComplete": ordered,
    "duplicateRowsBeyondFirst": result["adjacentDuplicateRowsObserved"] if ordered else None,
    "uniqueKeysVerified": ordered and result["adjacentDuplicateRowsObserved"] == 0,
    "duplicateCheckMeaning": "完整有效键非降序时，相邻键可覆盖全部重复；否则只报告相邻重复观察数",
  })
  return result


def auditAdjustments(path, asOf):
  """流式核验复权事件；受限字典允许检查乱序的同代码生效日组。"""
  reader = openParquet(path, EVENT_COLUMNS)
  tracker = AuditTracker(path, reader.metadata, EVENT_COUNTERS)
  fieldCounts = dict.fromkeys(EVENT_NUMERIC_FIELDS, 0)
  eventKeys = {}
  keysComplete = True
  try:
    for batch in reader.iter_batches(batch_size=BATCH_ROWS, columns=list(EVENT_COLUMNS), use_threads=False):
      for row in batch.to_pylist():
        code, day = tracker.observe(row, "ex_date_ms")
        ticker = row["ticker"]
        if (not isinstance(ticker, str) or not TICKER_PATTERN.fullmatch(ticker)
            or (code is not None and ticker != code[:6])):
          tracker.issue("invalidTickerRows", row)
        if row["currency"] != "CNY":
          tracker.issue("nonCnyRows", row)
        invalidFields = [field for field in EVENT_NUMERIC_FIELDS
                         if not isFiniteNumber(row[field]) or row[field] < 0]
        if invalidFields:
          tracker.issue("invalidNumericRows", row)
          for field in invalidFields:
            fieldCounts[field] += 1
        if day is not None:
          # 未来生效可能只是预告，分类计数不等同于错误判定。
          field = "futureEffectiveRows" if day > asOf else "effectiveOnOrBeforeAsOfRows"
          tracker.result[field] += 1
        if code is None or day is None:
          tracker.issue("invalidKeyRows", row)
          keysComplete = False
          continue
        key = (code, day.isoformat())
        if key in eventKeys:
          eventKeys[key] += 1
        elif len(eventKeys) < MAX_TRACKED_EVENT_KEYS:
          eventKeys[key] = 1
        else:
          keysComplete = False
  finally:
    reader.close()
  duplicateGroups = 0
  duplicateRows = 0
  groupExamples = []
  for (code, day), count in eventKeys.items():
    if count > 1:
      duplicateGroups += 1
      duplicateRows += count - 1
      if len(groupExamples) < MAX_EXAMPLES:
        groupExamples.append({"thscode": code, "effectiveDate": day, "rowsObserved": count})
  result = tracker.finish()
  result.update({
    "invalidNumericFieldCounts": fieldCounts,
    "duplicateCheckComplete": keysComplete,
    "duplicateEffectiveDateGroupCount": duplicateGroups if keysComplete else None,
    "duplicateRowsBeyondFirst": duplicateRows if keysComplete else None,
    "duplicateGroupCountLowerBound": duplicateGroups,
    "duplicateRowsBeyondFirstLowerBound": duplicateRows,
    "duplicateGroupExamples": groupExamples,
    "trackedEventKeyCount": len(eventKeys),
    "eventMeaning": "原始分红、送股和配股事件；生效日不是信息首次可知日期，重复组不自动视为非法",
  })
  return result


def auditDumps(dailyPath, adjustmentsPath, asOf):
  """审计两个本地 Parquet 并返回严格 JSON 字典；读取或契约错误抛出中文 ValueError。"""
  if type(asOf) is not date:
    raise ValueError("审计基准日期必须为 date，不能混用文本或时刻")
  results = {}
  for name, path, function in (("dailyK", dailyPath, auditDailyK),
                               ("adjustments", adjustmentsPath, auditAdjustments)):
    try:
      results[name] = function(path, asOf)
    except (OSError, pa.ArrowException) as error:
      raise ValueError(f"全市场审计：读取或解码文件失败：{path}（{type(error).__name__}）") from error
  return {
    "analysisType": "FULL_DUMP_DATA_QUALITY_ONLY",
    "status": "已完成全文件分批读取与统计，不代表质量问题全部通过",
    "createdAt": datetime.now(UTC).isoformat(), "asOf": asOf.isoformat(),
    "dateTimezone": "Asia/Shanghai，按 IANA 历史时区规则处理",
    "memoryLimits": {"batchRows": BATCH_ROWS, "trackedCodesPerFile": MAX_TRACKED_CODES,
                     "trackedDatesPerFile": MAX_TRACKED_DATES, "trackedEventKeys": MAX_TRACKED_EVENT_KEYS,
                     "examplesPerIssue": MAX_EXAMPLES},
    "fieldMeanings": {
      "actualRows": "实际完整解码的行数", "declaredRows": "Parquet 元数据声明的行数",
      "securityCount": "格式有效的不同证券代码数；集合达到上限后精确值为 null",
      "rowsByDate": "上海零点有效日期的实读行数，包含重复和其他字段异常的行",
      "dateMin/dateMax": "所有有效日期的最小和最大值，不表示中间日期或证券覆盖完整",
      "invalidDateRows": "日期非整数毫秒、非上海零点或超出可表示日期范围的行数",
      "invalidOhlcRows": "开高低收存在非有限、非正值或价位关系矛盾的行数",
      "invalidVolumeOrTurnoverRows": "成交量或成交额为负、空值或非有限数的行数",
      "zeroVolumeOrTurnoverRows": "成交量或成交额等于有效零值的行数，不等同异常或停牌",
      "nonStandardBasisRows": "币种、周期、复权口径不同时等于 CNY、1d、none 的行数",
      "invalidNumericRows": "复权事件任一数值字段为负、空值或非有限数的行数",
      "effectiveOnOrBeforeAsOfRows": "生效日不晚于基准日期的事件数，不代表当时已公告",
      "futureEffectiveRows": "基准日期之后生效的事件数，可能为预告",
      "uniqueKeysVerified": "日 K 全局排序已核验且没有重复，才为 true",
      "duplicateEffectiveDateGroupCount": "同证券同生效日出现多条事件的组数，不自动判非法",
    },
    "limitations": LIMITATIONS, **results,
  }


def parseAsOf(value):
  """严格解析 YYYY-MM-DD 格式的有效日期。"""
  try:
    parsed = date.fromisoformat(value)
  except ValueError:
    raise ValueError("审计基准日期无效，必须使用 YYYY-MM-DD 格式") from None
  if parsed.isoformat() != value:
    raise ValueError("审计基准日期无效，必须使用 YYYY-MM-DD 格式")
  return parsed


def main(argv=None):
  """解析输入、保护源文件并保存 UTF-8 无 BOM、LF 的审计报告，成功返回 0。"""
  parser = AuditArgumentParser(description="离线分批核验日 K 与复权事件，不联网、不清洗源数据")
  parser.add_argument("--daily-k", required=True, help="全市场日 K Parquet 路径")
  parser.add_argument("--adjustments", required=True, help="原始复权事件 Parquet 路径")
  parser.add_argument("--as-of", required=True, help="统计历史与未来事件的基准日期 YYYY-MM-DD")
  parser.add_argument("--output", required=True, help="审计 JSON 输出路径")
  try:
    arguments = parser.parse_args(argv)
    asOf = parseAsOf(arguments.as_of)
    output = Path(arguments.output).resolve()
    for source in (Path(arguments.daily_k).resolve(), Path(arguments.adjustments).resolve()):
      if output == source or (output.exists() and source.exists() and output.samefile(source)):
        raise ValueError("审计输出路径不能覆盖源 Parquet 文件")
    result = auditDumps(arguments.daily_k, arguments.adjustments, asOf)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8", newline="\n")
    print(f"全市场文件审计完成，报告已保存：{output}", flush=True)
    return 0
  except (OSError, ValueError) as error:
    print(f"全市场文件审计失败：{error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
  raise SystemExit(main())
