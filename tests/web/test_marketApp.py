"""真实行情本地服务的接口、隔离与安全边界测试。"""

from threading import Event

from fastapi.testclient import TestClient

from quant_forge.market.fuyaoClient import FuyaoError
from quant_forge.market.snapshotStore import SnapshotStore
from quant_forge.web.app import createApp


class FakeCollector:
  """提供可控采集结果，不进行真实网络请求。"""

  def collectIndices(self):
    return {"data": {"items": []}, "collectedAt": "2026-09-16T10:00:00+08:00", "rawResponses": []}

  collectStocks = collectIndices
  collectSectors = collectIndices


def testReadWithoutCollecting(tmp_path):
  """首次读取没有数据，不会自动触发采集。"""
  with TestClient(createApp(tmp_path, FakeCollector()), base_url="http://127.0.0.1") as client:
    result = client.get("/api/market").json()
    assert result["refreshing"] is False
    assert all(state["snapshot"] is None for state in result["modules"].values())
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/api/unknown").json()["error"]["message"] == "资源不存在"
    assert client.get("/outputs/market/indices/latest.json").status_code == 404


def testRefreshAndModuleFailure(tmp_path):
  """一个模块失败不阻止其余模块，并保留已有成功快照。"""
  collector = FakeCollector()
  store = SnapshotStore(tmp_path)
  old = store.saveSnapshot("stocks", collector.collectStocks())

  def fail():
    raise ValueError("股票分页不完整")

  collector.collectStocks = fail
  app = createApp(tmp_path, collector)
  with TestClient(app, base_url="http://127.0.0.1") as client:
    token = client.get("/api/session").json()["token"]
    assert client.post("/api/market/refresh", headers={"X-CSRF-Token": token}).status_code == 202
    app.state.marketService.waitForRefresh()
    modules = client.get("/api/market").json()["modules"]
    assert modules["indices"]["snapshot"] is not None
    assert modules["sectors"]["snapshot"] is not None
    assert modules["stocks"]["snapshot"]["snapshotId"] == old["snapshotId"]
    assert modules["stocks"]["error"]


def testSecurityAndConcurrentRefresh(tmp_path):
  """跨站及无令牌写请求拒绝，重复刷新拒绝。"""
  gate = Event()
  collector = FakeCollector()
  original = collector.collectIndices
  collector.collectIndices = lambda: (gate.wait(5), original())[1]
  app = createApp(tmp_path, collector)
  with TestClient(app, base_url="http://127.0.0.1") as client:
    token = client.get("/api/session").json()["token"]
    assert client.post("/api/market/refresh").status_code == 403
    assert client.get("/api/market", headers={"Host": "evil.example"}).status_code == 400
    headers = {"X-CSRF-Token": token, "Origin": "https://evil.example"}
    assert client.post("/api/market/refresh", headers=headers).status_code == 403
    headers = {"X-CSRF-Token": token}
    try:
      assert client.post("/api/market/refresh", headers=headers).status_code == 202
      assert client.post("/api/market/refresh", headers=headers).status_code == 409
    finally:
      gate.set()
      app.state.marketService.waitForRefresh()


def testMissingKeyAllowsReadingAndExplainsFailure(tmp_path, monkeypatch):
  """缺少密钥仍可查看快照，仅手动刷新时显示明确原因。"""
  def missingKey():
    raise FuyaoError("同花顺客户端：未配置 HITHINK_FINANCE_API_KEY")

  monkeypatch.setattr("quant_forge.market.fuyaoClient.readApiKey", missingKey)
  store = SnapshotStore(tmp_path)
  store.saveSnapshot("indices", FakeCollector().collectIndices())
  app = createApp(tmp_path)
  with TestClient(app, base_url="http://127.0.0.1") as client:
    assert client.get("/api/market").json()["modules"]["indices"]["snapshot"]
    token = client.get("/api/session").json()["token"]
    client.post("/api/market/refresh", headers={"X-CSRF-Token": token})
    app.state.marketService.waitForRefresh()
    assert "HITHINK_FINANCE_API_KEY" in client.get("/api/market").json()["modules"]["indices"]["error"]


def testDamagedModuleDoesNotBreakOthers(tmp_path):
  """一个模块指针损坏时其他模块仍可读取，重新采集可恢复。"""
  store = SnapshotStore(tmp_path)
  store.saveSnapshot("indices", FakeCollector().collectIndices())
  (tmp_path / "indices" / "latest.json").write_text("[]", encoding="utf-8")
  app = createApp(tmp_path, FakeCollector())
  with TestClient(app, base_url="http://127.0.0.1") as client:
    result = client.get("/api/market").json()
    assert result["modules"]["indices"]["error"]
    assert result["modules"]["stocks"]["error"] is None
    token = client.get("/api/session").json()["token"]
    client.post("/api/market/refresh", headers={"X-CSRF-Token": token})
    app.state.marketService.waitForRefresh()
    assert client.get("/api/market").json()["modules"]["indices"]["error"] is None


def testSlowSectorDoesNotBlockStockCollection(tmp_path):
  """板块请求耗时较长时，个股模块仍能独立完成采集。"""
  sectorGate = Event()
  stockCompleted = Event()
  collector = FakeCollector()
  collector.collectSectors = lambda: (sectorGate.wait(5), FakeCollector().collectSectors())[1]
  collector.collectStocks = lambda: (stockCompleted.set(), FakeCollector().collectStocks())[1]
  app = createApp(tmp_path, collector)
  with TestClient(app, base_url="http://127.0.0.1") as client:
    token = client.get("/api/session").json()["token"]
    client.post("/api/market/refresh", headers={"X-CSRF-Token": token})
    try:
      assert stockCompleted.wait(1), "板块采集不应阻塞个股采集"
    finally:
      sectorGate.set()
      app.state.marketService.waitForRefresh()
