"""从固定双轮分析记录运行新闻旁路实验：python -m quant_forge.research.newsPilot。"""
# ruff: noqa: N999

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

from quant_forge.config import config_snapshot_id, load_default_config
from quant_forge.domain.models import PermissionGrade
from quant_forge.research.newsReview import candidateHash, requireText, reviewCandidate
from quant_forge.rules.position import decide_position
from quant_forge.rules.risk import assess_risk

BASE_GRADE = PermissionGrade.C
REPORT_STEM = "news-pilot"


def indexRecords(records: list, key: str) -> dict:
  """按唯一标识索引数组；错误输入抛出中文 ValueError。"""
  if not isinstance(records, list):
    raise TypeError("输入必须为 JSON 数组")
  indexed = {}
  for record in records:
    if not isinstance(record, dict):
      raise TypeError("记录必须为 JSON 对象")
    identity = requireText(record.get(key), key)
    if identity in indexed:
      raise ValueError(f"输入包含重复 {key}：{identity}")
    indexed[identity] = record
  return indexed


def calculateImpact(event):
  """复用现有新闻风险及仓位规则；以无外围价格风险、基础 C 级为假设。"""
  risk = assess_risk([], [] if event is None else [event])
  position = decide_position(
    BASE_GRADE, risk.level, positive_adjustment=bool(risk.positive_factors) and not risk.negative_factors,
  )
  return risk.level.value, position.final_grade.value, position.total.maximum


def runPilot(cases: list, predictions: list, reviews: list) -> dict:
  """执行离线单事件敏感性实验，返回可序列化报告；重复或未知标识抛出 ValueError。

  此函数不生成 PreMarketDecision、不调用发布入口，也不推断收益或模型准确率。
  """
  startedAt = perf_counter()
  indexedCases = indexRecords(cases, "id")
  indexedPredictions = indexRecords(predictions, "caseId")
  indexedReviews = indexRecords(reviews, "caseId")
  if not indexedCases:
    raise ValueError("实验至少需要一条样本")
  if (indexedPredictions.keys() | indexedReviews.keys()) - indexedCases.keys():
    raise ValueError("提取或审查结果包含未知样本")
  rows = []
  for identity, case in indexedCases.items():
    if case.get("kind") not in ("real", "synthetic"):
      raise ValueError("样本 kind 必须为 real 或 synthetic")
    checked = reviewCandidate(case, indexedPredictions.get(identity), indexedReviews.get(identity))
    candidateRisk, candidateGrade, _ = calculateImpact(checked["candidateEvent"])
    shadowRisk, shadowGrade, shadowMax = calculateImpact(checked["acceptedEvent"])
    rows.append({
      "caseId": identity, "title": case.get("title", identity), "kind": case["kind"],
      "status": checked["status"], "issues": checked["issues"], "sourceCount": checked["sourceCount"],
      "baselineGrade": BASE_GRADE.value,
      "candidateRisk": candidateRisk if checked["candidateEvent"] else None,
      "candidateGrade": candidateGrade if checked["candidateEvent"] else None,
      "shadowRisk": shadowRisk, "shadowGrade": shadowGrade, "shadowMaxPosition": shadowMax,
    })
  config = load_default_config()
  return {
    "mode": "offline-recorded-two-pass", "formalEligible": False,
    "inputHash": candidateHash({"cases": cases, "predictions": predictions, "reviews": reviews}),
    "configVersion": config.version, "configHash": config_snapshot_id(config),
    "modelAccuracy": None, "modelCost": None, "modelLatencySeconds": None,
    "replaySeconds": round(perf_counter() - startedAt, 6),
    "counts": {
      "total": len(rows), "accepted": sum(row["status"] == "ACCEPTED" for row in rows),
      "review": sum(row["status"] == "REVIEW" for row in rows),
      "real": sum(row["kind"] == "real" for row in rows),
      "synthetic": sum(row["kind"] == "synthetic" for row in rows),
    },
    "rows": rows,
  }


def escapeCell(value: object) -> str:
  """转义报告表格文本，避免来源内容改变 Markdown 表格或注入 HTML。"""
  return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(
    ">", "&gt;",
  ).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def renderReport(report: dict) -> str:
  """生成明确标记研究边界的中文 Markdown 报告。"""
  lines = [
    "# 新闻分析旁路实验", "",
    "本报告回放已保存的提取与审查记录，不会调用在线模型或修改正式盘前结论。",
    "缺少独立人工金标准，模型准确率、模型耗时、模型费用均为未知；回放耗时仅为本地计算时间。",
    "基础权限统一假设为 C、无外围行情冲击。每行单独计算，不代表任何真实交易日或组合回测。",
    "候选权限表示跳过准入检查直接采用该标签的假设结果；旁路权限仅采用通过审查的记录。",
    "被拒绝的事件不会加入敏感性计算；NONE 仅表示没有已采纳的风险标签，不能证明市场无风险。",
    "ACCEPTED 仅表示检查通过；片段存在不等于语义正确，模型置信度未经校准。", "",
    f"样本总数：{report['counts']['total']}；可采纳：{report['counts']['accepted']}；",
    f"待核验：{report['counts']['review']}；真实材料：{report['counts']['real']}；",
    f"合成材料：{report['counts']['synthetic']}。", "",
    "| 样本 | 类型 | 审查状态 | 去重来源数 | 候选权限 | 旁路权限 | 旁路风险 | 原因 |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
  ]
  for row in report["rows"]:
    cells = [row["caseId"], row["kind"], row["status"], row["sourceCount"], row["candidateGrade"] or "不可计算",
             row["shadowGrade"], row["shadowRisk"], "；".join(row["issues"]) or "机械检查及记录审查通过"]
    lines.append("| " + " | ".join(escapeCell(cell) for cell in cells) + " |")
  lines.extend([
    "", f"输入摘要：`{report['inputHash']}`", f"规则配置版本：`{report['configVersion']}`",
    f"规则配置摘要：`{report['configHash']}`", f"本地回放耗时：{report['replaySeconds']} 秒。", "",
  ])
  return "\n".join(lines)


def uniqueObject(pairs: list) -> dict:
  """JSON 解析时拒绝重复字段，避免结果被后面的同名字段覆盖。"""
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError(f"JSON 对象包含重复字段：{key}")
    result[key] = value
  return result


def rejectConstant(value: str):
  """拒绝 JSON 中非标准的非有限数字常量。"""
  raise ValueError("JSON 包含非有限数值")


def main(argv: list[str] | None = None) -> int:
  """读取三个 JSON 数组并写独立实验报告；读写或校验失败输出中文错误并返回 1。"""
  parser = argparse.ArgumentParser(description="新闻提取与证据审查的离线旁路验证")
  parser.add_argument("--input-dir", type=Path, required=True, help="包含 cases/predictions/reviews.json 的目录")
  parser.add_argument("--output-dir", type=Path, required=True, help="独立研究报告目录")
  args = parser.parse_args(argv)
  try:
    records = [json.loads((args.input_dir / f"{name}.json").read_text(encoding="utf-8"),
                          object_pairs_hook=uniqueObject, parse_constant=rejectConstant)
               for name in ("cases", "predictions", "reviews")]
    report = runPilot(*records)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{REPORT_STEM}.json").write_text(
      json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n",
    )
    (output / f"{REPORT_STEM}.md").write_text(renderReport(report), encoding="utf-8", newline="\n")
  except (OSError, ValueError, TypeError, KeyError) as error:
    print(f"新闻旁路实验失败：{error}", file=sys.stderr)
    return 1
  print(f"新闻旁路实验完成：{report['counts']['total']} 条，待核验 {report['counts']['review']} 条。")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
