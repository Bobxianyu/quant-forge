"""继续真实股票采集，复用已取得的昨日历史缓存。"""
import json

from quant_forge.market.collector import FuyaoCollector
from quant_forge.market.snapshotStore import SnapshotStore

collector = FuyaoCollector(cacheDir="outputs/market/cache")
result = collector.collectStocks()
snapshot = SnapshotStore("outputs/market").saveSnapshot("stocks", result)
print(json.dumps({"snapshotId": snapshot["snapshotId"], "coverage": snapshot["data"]["coverage"]}, ensure_ascii=True))
