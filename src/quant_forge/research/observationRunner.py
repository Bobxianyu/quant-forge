"""复用冻结真实数据，分阶段运行独立观察研究；无网络调用，不覆盖成功产物。

入口：python -m quant_forge.research.observationRunner build|select|holdout。
默认输出 outputs/research/observation-study-20260927，需已安装项目 web 可选依赖中的 pyarrow。
"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quant_forge.research.observationEvaluation import (
  EQUAL_WEIGHTS,
  GATES,
  LIQUIDITY_WEIGHTS,
  PERIODS,
  chooseModel,
  evaluateModel,
)
from quant_forge.research.observationPanel import buildCodePanel
from quant_forge.research.observationStudy import FEATURE_NAMES, HISTORY_ROWS, rankCandidates, weightGrid
from quant_forge.research.poolHistory import validatePoolArtifact

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path("outputs/research/observation-study-20260927")
POOL_ROOT = Path("outputs/research/year-pool-audit-20260919")
DUMP_ROOT = Path("outputs/fuyao-probe/history-full-dumps-20260918T071143987255Z")
ENVIRONMENT_RESULT = Path("outputs/research/environment-replay-20260918/result.json")
PLAN = Path("docs/research/observation-study-plan-20260927.md")
SHANGHAI = ZoneInfo("Asia/Shanghai")
BATCH_ROWS = 8192
OUTCOME_FIELDS = ("downsidePct", "upsidePct", "closePct", "touchEvent", "sealEvent")
SOURCE_MODULES = ("observationStudy.py", "observationPanel.py", "observationEvaluation.py", "observationRunner.py")
PANEL_SCHEMA = pa.schema([(key, pa.string()) for key in ("date", "signalDate", "code")]
                         + [(key, pa.float64()) for key in (*FEATURE_NAMES, *OUTCOME_FIELDS[:3])]
                         + [(key, pa.bool_()) for key in OUTCOME_FIELDS[3:]])


def readJson(path):
  """读取 UTF-8 研究记录，格式异常由主入口统一报告。"""
  return json.loads(path.read_text(encoding="utf-8"))


def digestFile(path):
  """计算文件内容摘要，流式处理大文件，不执行数据质量全扫描。"""
  with path.open("rb") as source:
    return hashlib.file_digest(source, "sha256").hexdigest()


def writeFrozen(path, value):
  """首次写入严格 JSON 及独立摘要，已有路径拒绝覆盖，异常由调用者处理。"""
  sidecar = Path(str(path) + ".sha256")
  if path.exists() or sidecar.exists():
    raise FileExistsError(f"研究证据已存在，禁止覆盖：{path}")
  content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
  with path.open("xb") as target:
    target.write(content)
  with sidecar.open("x", encoding="utf-8", newline="\n") as target:
    target.write(hashlib.sha256(content).hexdigest() + "\n")


def readFrozen(path):
  """校验独立摘要后复用研究 JSON，拒绝静默重签认改变的记录。"""
  expected = Path(str(path) + ".sha256").read_text(encoding="utf-8").strip()
  if digestFile(path) != expected:
    raise ValueError(f"研究证据摘要不一致：{path}")
  return readJson(path)


def _evidence(root, path, expected=None):
  """限定证据在工作区，必要时匹配上游冻结摘要。"""
  full = (root / path).resolve()
  if not full.is_relative_to(root.resolve()):
    raise ValueError("研究证据路径超出工作区")
  digest = digestFile(full)
  if expected and digest != expected:
    raise ValueError(f"上游证据摘要变化：{path}")
  return {"path": full.relative_to(root).as_posix(), "sha256": digest}


def _dateMs(day):
  """近期上海交易日零点毫秒，仅用于日期过滤。"""
  return int(datetime.combine(date.fromisoformat(day), time.min, SHANGHAI).timestamp()) * 1000


def _day(timestamp):
  """使用上海历史时区还原供应商日期。"""
  return datetime.fromtimestamp(timestamp / 1000, SHANGHAI).date().isoformat()


def _freezeInputs(root):
  """核对已有证据并返回本研究输入，不重复网络请求或历史价格对账。"""
  progress = readJson(root / POOL_ROOT / "collection-progress.json")
  query = readJson(root / POOL_ROOT / "collection-input.json")
  if progress["status"] != "completed" or progress["completedPools"] != 486:
    raise ValueError("连续一年双池尚未完整冻结")
  sources, pools = [], defaultdict(dict)
  reference = query["referenceCalendar"]
  sources.append(_evidence(root, reference["path"], reference["sha256"]))
  calendar = sorted(_day(row["date_ms"]) for row in
                    readJson(root / reference["path"])["records"][0]["response"]["data"]["item"])
  for entry in progress["completed"]:
    source = entry["source"]
    sources.append(_evidence(root, source["path"], source["sha256"]))
    checked = validatePoolArtifact(readJson(root / source["path"]), entry["pool"], date.fromisoformat(entry["day"]))
    rows = checked["rows"]
    key = "up" if entry["pool"] == "limit-up-pool" else "break"
    if key in pools[entry["day"]]:
      raise ValueError("完整池记录重复")
    pools[entry["day"]][key] = {row["thscode"] for row in rows}
  dumpManifest = readJson(root / DUMP_ROOT / "manifest.json")
  for item in dumpManifest["files"]:
    sources.append(_evidence(root, item["path"], item["sha256"]))
  sources.append(_evidence(root, ENVIRONMENT_RESULT))
  sources.append(_evidence(root, POOL_ROOT / "result.json"))
  sources.append(_evidence(root, PLAN))
  implementations = [_evidence(root, Path("src/quant_forge/research") / name) for name in SOURCE_MODULES]
  environment = readJson(root / ENVIRONMENT_RESULT)["result"]
  grades = {row["targetDate"]: row["permissionGrade"] for row in environment["observations"]}
  targets = sorted(day for day in pools if PERIODS["train"][0] <= day <= PERIODS["holdout"][1])
  if targets != [day for day in calendar if PERIODS["train"][0] <= day <= PERIODS["holdout"][1]]:
    raise ValueError("双池日期与冻结参考日历不一致")
  for row in environment["observations"]:
    if row["signalDate"] != calendar[calendar.index(row["targetDate"]) - 1]:
      raise ValueError("环境信号日不是前一参考交易日")
  manifest = {"createdAt": datetime.now(UTC).isoformat(), "studyType": "INDEPENDENT_OBSERVATION_ONLY",
              "periods": PERIODS, "weights": weightGrid(), "gates": GATES,
              "sources": sources, "implementations": implementations,
              "dateGrades": {day: grades.get(day) for day in targets},
              "sourceCoverageGaps": readJson(root / POOL_ROOT / "result.json")["missingDailyDistinctCodes"],
              "limitations": ["全体候选历史交易资格未知", "当前文件不是无偏历史证券全集",
                              "池未命中为未知标签，事件比例仅为记录下界", "事后补采不证明历史可得性",
                              "不输出账户回撤，不替换正式推荐"]}
  return manifest, calendar, pools


def buildStudy(root, output):
  """读取必要日期与列构建面板，产物已完成则核对并复用，不重新构建。"""
  output.mkdir(parents=True, exist_ok=True)
  if (output / "panel-manifest.json").exists():
    return verifyPanel(root, output)[1]
  if (output / "input-manifest.json").exists():
    raise ValueError("发现未完成面板构建，须先核对进度和临时文件，不自动覆盖")
  manifest, calendar, pools = _freezeInputs(root)
  writeFrozen(output / "input-manifest.json", manifest)
  targets = sorted(manifest["dateGrades"])
  first = calendar[calendar.index(targets[0]) - HISTORY_ROWS]
  last = targets[-1]
  events = defaultdict(set)
  expression = (ds.field("ex_date_ms") >= _dateMs(first)) & (ds.field("ex_date_ms") <= _dateMs(last))
  eventRows = ds.dataset(root / DUMP_ROOT / "adjustment-factors.parquet").to_table(
    columns=["thscode", "ex_date_ms"], filter=expression).to_pylist()
  for row in eventRows:
    events[row["thscode"]].add(_day(row["ex_date_ms"]))
  expression = (ds.field("date_ms") >= _dateMs(first)) & (ds.field("date_ms") <= _dateMs(last))
  columns = ["thscode", "date_ms", "open_price", "high_price", "low_price", "close_price", "volume", "turnover"]
  scanner = ds.dataset(root / DUMP_ROOT / "daily-k.parquet").scanner(
    columns=columns, filter=expression, batch_size=BATCH_ROWS, use_threads=False)
  partial = output / "panel.partial.parquet"
  if partial.exists():
    raise FileExistsError("面板临时文件已存在，禁止覆盖")
  stats, reasons, dailyCounts = Counter(), Counter(), Counter()
  exclusionsByCode = {}
  currentCode, bars, buffer = None, [], []
  with pq.ParquetWriter(partial, PANEL_SCHEMA, compression="zstd") as writer:
    def flushCode(code, records):
      """只保存每只股票局部历史，写入前不查看留出聚合结果。"""
      rows, excluded = buildCodePanel(code, records, calendar, pools, events[code], targets)
      stats["sourceCodes"] += 1
      stats["panelRows"] += len(rows)
      reasons.update(excluded)
      exclusionsByCode[code] = excluded
      dailyCounts.update(row["date"] for row in rows)
      buffer.extend(rows)
      if len(buffer) >= BATCH_ROWS:
        writer.write_table(pa.Table.from_pylist(buffer, schema=PANEL_SCHEMA))
        buffer.clear()
      if stats["sourceCodes"] % 1000 == 0:
        print(json.dumps({"已处理证券": stats["sourceCodes"], "已构造观察行": stats["panelRows"]}), flush=True)

    for batch in scanner.to_batches():
      for row in batch.to_pylist():
        code = row.pop("thscode")
        row["date"] = _day(row.pop("date_ms"))
        if currentCode is not None and code != currentCode:
          if code < currentCode:
            raise ValueError("源日线证券排序变化，不能按证券流式构建")
          flushCode(currentCode, bars)
          bars = []
        currentCode = code
        bars.append(row)
    if currentCode is not None:
      flushCode(currentCode, bars)
    if buffer:
      writer.write_table(pa.Table.from_pylist(buffer, schema=PANEL_SCHEMA))
  panelPath = output / "panel.parquet"
  if panelPath.exists():
    raise FileExistsError("完整面板已存在，禁止覆盖")
  partial.rename(panelPath)
  writeFrozen(output / "exclusions.json", {"counts": dict(reasons), "byCode": exclusionsByCode})
  result = {"createdAt": datetime.now(UTC).isoformat(), "status": "completed", **dict(stats),
            "inputSha256": digestFile(output / "input-manifest.json"), "panelSha256": digestFile(panelPath),
            "dailyCandidateCounts": dict(sorted(dailyCounts.items())), "exclusionCounts": dict(reasons),
            "historicalEligibility": "UNKNOWN_FOR_ALL_RESEARCH_CANDIDATES"}
  writeFrozen(output / "panel-manifest.json", result)
  return result


def verifyPanel(root, output):
  """后续阶段只验实现、输入清单和面板摘要，不重跑原始数据扫描。"""
  inputs = readFrozen(output / "input-manifest.json")
  panel = readFrozen(output / "panel-manifest.json")
  if digestFile(output / "input-manifest.json") != panel["inputSha256"]:
    raise ValueError("输入清单摘要变化")
  if digestFile(output / "panel.parquet") != panel["panelSha256"]:
    raise ValueError("研究面板摘要变化")
  for source in inputs["implementations"]:
    _evidence(root, source["path"], source["sha256"])
  return inputs, panel


def loadPanel(path, dateGrades, *, holdout):
  """按日期列过滤后才读取结果列；选择阶段不加载最终留出标签。"""
  start = PERIODS["holdout"][0]
  dates = {day: grade for day, grade in dateGrades.items() if (day >= start) == holdout}
  days = {day: {"grade": grade, "rows": []} for day, grade in dates.items()}
  expression = ds.field("date") >= start if holdout else ds.field("date") < start
  for batch in ds.dataset(path).scanner(filter=expression, batch_size=BATCH_ROWS).to_batches():
    for row in batch.to_pylist():
      days[row["date"]]["rows"].append({"code": row["code"],
        "features": {key: row[key] for key in FEATURE_NAMES},
        "outcome": {key: row[key] for key in OUTCOME_FIELDS}})
  for item in days.values():
    item["rows"] = rankCandidates(item["rows"])
  return days


def selectStudy(root, output):
  """仅训练/验证选择，并将方案写入不可覆盖文件，留出结果不参与本函数。"""
  inputs, panel = verifyPanel(root, output)
  path = output / "selection.json"
  if path.exists():
    return readFrozen(path)
  days = loadPanel(output / "panel.parquet", inputs["dateGrades"], holdout=False)
  result = chooseModel(days)
  result.update(createdAt=datetime.now(UTC).isoformat(), panelSha256=panel["panelSha256"],
                inputSha256=panel["inputSha256"])
  writeFrozen(path, result)
  return result


def evaluateHoldout(root, output):
  """读取已锁定选择后才打开留出标签，输出固定权重及环境对照，不再选权重。"""
  inputs, panel = verifyPanel(root, output)
  selection = readFrozen(output / "selection.json")
  if selection["panelSha256"] != panel["panelSha256"] or selection["inputSha256"] != panel["inputSha256"]:
    raise ValueError("锁定选择与面板版本不匹配")
  path = output / "holdout.json"
  if path.exists():
    return readFrozen(path)
  days = loadPanel(output / "panel.parquet", inputs["dateGrades"], holdout=True)
  baselines = {"equal": evaluateModel(days, EQUAL_WEIGHTS, "ALL", includeDaily=True),
               "liquidity": evaluateModel(days, LIQUIDITY_WEIGHTS, "ALL"),
               "universe": evaluateModel(days, EQUAL_WEIGHTS, "ALL", allCandidates=True)}
  winner = selection["selected"]
  result = {"createdAt": datetime.now(UTC).isoformat(), "selectionSha256": digestFile(output / "selection.json"),
            "baselines": baselines, "selected": None, "limitations": inputs["limitations"]}
  if winner:
    weights, gate = winner["weights"], winner["gate"]
    result["selected"] = {"id": winner["id"], "weights": weights, "gate": gate,
                          "metrics": evaluateModel(days, weights, gate, includeDaily=True)}
    result["fixedWeightEnvironmentComparison"] = {mode: evaluateModel(days, weights, mode) for mode in GATES}
    blocked = {day: item for day, item in days.items() if item["grade"] and item["grade"] not in GATES[gate]}
    matched = {day: item for day, item in days.items() if item["grade"] in GATES[gate]}
    result["blockedDayObservations"] = evaluateModel(blocked, weights, "ALL")
    result["equalBaselineOnSameEnvironmentDays"] = evaluateModel(matched, EQUAL_WEIGHTS, "ALL")
    metrics = result["selected"]["metrics"]
    baseline = baselines["equal"]
    result["holdoutRiskImproved"] = bool(metrics["tailDownsidePct"] is not None
      and baseline["tailDownsidePct"] is not None
      and metrics["tailDownsidePct"] < baseline["tailDownsidePct"]
      and metrics["meanDownsidePct"] <= baseline["meanDownsidePct"])
  writeFrozen(path, result)
  return result


def main():
  """运行明确阶段，异常写中文错误及完整堆栈；不接触鉴权配置。"""
  parser = argparse.ArgumentParser(description="独立盘前观察研究：构建、选择、留出验证")
  parser.add_argument("stage", choices=("build", "select", "holdout"))
  args = parser.parse_args()
  functions = {"build": buildStudy, "select": selectStudy, "holdout": evaluateHoldout}
  try:
    result = functions[args.stage](ROOT, ROOT / OUTPUT)
    print(json.dumps({"阶段": args.stage, "状态": result.get("status", "completed"),
                      "输出": OUTPUT.as_posix()}, ensure_ascii=False), flush=True)
  except (OSError, ValueError, KeyError, TypeError) as error:
    print(f"{datetime.now(UTC).isoformat()} [observationRunner] ERROR 研究运行失败：{error}", file=sys.stderr)
    raise


if __name__ == "__main__":
  main()
