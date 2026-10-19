"""验证快照持久化和失败时保留已有数据。"""

import json

import pytest

from quant_forge.market.snapshotStore import SnapshotStore


def payload(value=1):
  """创建不含凭据的最小采集结果。"""
  return {"data": {"items": [value]}, "collectedAt": "2026-09-16T10:00:00+08:00",
          "source": "同花顺 Fuyao", "rawResponses": [{"code": 0}]}


def testHistoryAndRestart(tmp_path):
  """新采集不覆盖历史，重启可读取最新版本。"""
  store = SnapshotStore(tmp_path)
  assert store.readModule("indices")["snapshot"] is None
  first = store.saveSnapshot("indices", payload())
  second = store.saveSnapshot("indices", payload(2))
  assert first["snapshotId"] != second["snapshotId"]
  assert len(list((tmp_path / "indices" / "history").glob("*.json"))) == 2
  state = SnapshotStore(tmp_path).readModule("indices")
  assert state["snapshot"]["data"]["items"] == [2]
  assert "rawResponses" not in state["snapshot"]
  historyPath = next((tmp_path / "indices" / "history").glob("*.json"))
  assert json.loads(historyPath.read_text(encoding="utf-8"))["rawResponses"]


def testFailurePreservesSnapshot(tmp_path):
  """刷新失败可跨重启保留原快照与中文错误。"""
  store = SnapshotStore(tmp_path)
  first = store.saveSnapshot("stocks", payload())
  store.recordFailure("stocks", "个股采集失败")
  state = SnapshotStore(tmp_path).readModule("stocks")
  assert state["snapshot"]["snapshotId"] == first["snapshotId"]
  assert state["error"] == "个股采集失败"
  store.saveSnapshot("stocks", payload(3))
  assert store.readModule("stocks")["error"] is None


def testPointerFailurePreservesSnapshot(tmp_path, monkeypatch):
  """最新指针写入失败不改变已发布数据。"""
  store = SnapshotStore(tmp_path)
  first = store.saveSnapshot("indices", payload())
  monkeypatch.setattr(store, "writeState", lambda *args: (_ for _ in ()).throw(OSError("磁盘写入失败")))
  with pytest.raises(OSError):
    store.saveSnapshot("indices", payload(2))
  assert store.readModule("indices")["snapshot"]["snapshotId"] == first["snapshotId"]


@pytest.mark.parametrize("module", ["../escape", "", "history"])
def testInvalidModuleRejected(tmp_path, module):
  """未知模块不能变成文件路径。"""
  with pytest.raises(ValueError, match="模块"):
    SnapshotStore(tmp_path).readModule(module)


def testNonFiniteValuesRejected(tmp_path):
  """非有限数值不能进入可读快照。"""
  store = SnapshotStore(tmp_path)
  with pytest.raises(ValueError):
    store.saveSnapshot("indices", payload(float("nan")))
  assert store.readModule("indices")["snapshot"] is None


@pytest.mark.parametrize("content", ["[]", "null", '{"snapshotId":"../../x"}'])
def testDamagedStateRejected(tmp_path, content):
  """损坏的状态必须产生可隔离的校验错误。"""
  directory = tmp_path / "indices"
  directory.mkdir()
  (directory / "latest.json").write_text(content, encoding="utf-8")
  with pytest.raises(ValueError, match="快照"):
    SnapshotStore(tmp_path).readModule("indices")
