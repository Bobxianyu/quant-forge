"""将已下载的真实批量历史与此前逐股历史逐项对账。"""
import json
import math
from pathlib import Path

from quant_forge.market.dumpHistory import DumpHistory

artifact = Path("outputs/fuyao-dump/daily-k-10d-10081a3ba9c94001b8e0b88a0db526c5.parquet")
turnovers, rowCount, dates = DumpHistory("outputs/fuyao-dump").readTurnovers(artifact, "2026-09-15")
differences = []
matched = 0
for path in Path("outputs/market/cache/2026-09-15").glob("*.json"):
  payload = json.loads(path.read_text(encoding="utf-8"))
  original = payload["response"]["data"]["item"][0]["turnover"]
  actual = turnovers.get(path.stem)
  if actual is None or not math.isclose(actual, original, rel_tol=0, abs_tol=0.01):
    differences.append(path.stem)
  else:
    matched += 1
print(json.dumps({"matched": matched, "differences": differences, "rowCount": rowCount, "dates": sorted(dates)}))
