"""按证券构造独立观察面板，盘前资格与事后结果分开计算。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import re
from collections import Counter

from quant_forge.research.observationStudy import HISTORY_ROWS, buildFeatures, buildOutcome

CODE_SCOPE = re.compile(r"(?:(?:000|001|002|003)\d{3}\.SZ|(?:600|601|603|605)\d{3}\.SH)")


def supportedCode(code):
  """只判固定代码范围，不将它解释为历史板块、上市或 ST 资格证明。"""
  return isinstance(code, str) and CODE_SCOPE.fullmatch(code) is not None


def buildCodePanel(code, rows, calendar, pools, eventDates, targetDates):
  """返回单证券观察行与互斥排除原因计数，T 行缺失仍保留候选。

  参数：rows 是同一证券的日线列表，每行含 date；calendar 为完整有序参考日历。
  pools 为日期到 up/break 代码集合，eventDates 为该证券已记录除权生效日集合。
  返回：(面板列表, 排除计数字典)；重复行、日历或缺少必要池证据时抛出中文 ValueError。
  """
  if calendar != sorted(set(calendar)):
    raise ValueError("参考日历必须严格递增且无重复")
  if not supportedCode(code):
    return [], {"OUTSIDE_CODE_SCOPE": len(targetDates)}
  byDate = {row["date"]: row for row in rows}
  if len(byDate) != len(rows):
    raise ValueError(f"同证券日线日期重复：{code}")
  positions = {day: i for i, day in enumerate(calendar)}
  panel, excluded = [], Counter()
  for target in targetDates:
    index = positions.get(target, 0)
    if index < HISTORY_ROWS:
      excluded["MISSING_HISTORY"] += 1
      continue
    expected = calendar[index - HISTORY_ROWS:index]
    signal = expected[-1]
    if any(day not in pools or set(pools[day]) != {"up", "break"} for day in (signal, target)):
      raise ValueError(f"缺少完整历史双池：{signal} / {target}")
    if code in pools[signal]["up"] or code in pools[signal]["break"]:
      excluded["PREVIOUS_POOL_EVENT"] += 1
      continue
    # 在读取目标日行情和事件标签之前完成全部候选判断。
    features, reason = buildFeatures([byDate.get(day) for day in expected], expected, eventDates)
    if reason:
      excluded[reason] += 1
      continue
    outcome = buildOutcome(byDate.get(target), code in pools[target]["up"], code in pools[target]["break"])
    panel.append({"date": target, "signalDate": signal, "code": code, **features, **outcome})
  return panel, dict(excluded)
