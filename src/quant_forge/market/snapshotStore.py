"""保存不可变行情历史，并以原子指针发布各模块最新快照。"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

MODULES = ("indices", "sectors", "stocks")
SNAPSHOT_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def currentTime():
  """返回带时区的真实操作时间。"""
  return datetime.now(UTC).isoformat()


class SnapshotStore:
  """管理快照文件；参数 root 为专用数据目录，文件损坏时显式报错。"""

  def __init__(self, root):
    self.root = Path(root)
    self.lock = RLock()

  def modulePath(self, module):
    """只允许白名单模块，非法输入抛出 ValueError。"""
    if module not in MODULES:
      raise ValueError("未知行情模块")
    return self.root / module

  def readState(self, module):
    """读取发布指针及最近尝试状态；无记录时返回空状态。"""
    path = self.modulePath(module) / "latest.json"
    if not path.exists():
      return {"snapshotId": None, "error": None, "lastAttemptAt": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or "snapshotId" not in state:
      raise ValueError("快照指针结构损坏")
    snapshotId = state.get("snapshotId")
    if snapshotId is not None and not SNAPSHOT_PATTERN.fullmatch(str(snapshotId)):
      raise ValueError("快照指针损坏")
    return state

  def readModule(self, module):
    """返回浏览器可读状态，不向浏览器暴露原始响应。"""
    with self.lock:
      state = self.readState(module)
      snapshot = None
      if state["snapshotId"]:
        path = self.modulePath(module) / "history" / f"{state['snapshotId']}.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("data"), dict):
          raise ValueError("快照内容结构损坏")
        snapshot.pop("rawResponses", None)
      return {"snapshot": snapshot, "error": state.get("error"), "lastAttemptAt": state.get("lastAttemptAt")}

  def writeState(self, module, state):
    """先写临时文件再原子替换指针；失败保留原指针。"""
    directory = self.modulePath(module)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
      with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(state, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(temporary, directory / "latest.json")
    finally:
      temporary.unlink(missing_ok=True)

  def saveSnapshot(self, module, result):
    """完整保存采集结果后发布；序列化或磁盘失败不改变最新版本。"""
    with self.lock:
      directory = self.modulePath(module) / "history"
      snapshotId = uuid4().hex
      snapshot = {**result, "snapshotId": snapshotId, "savedAt": currentTime()}
      content = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
      directory.mkdir(parents=True, exist_ok=True)
      with (directory / f"{snapshotId}.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
      self.writeState(module, {"snapshotId": snapshotId, "error": None, "lastAttemptAt": currentTime()})
      return self.readModule(module)["snapshot"]

  def recordFailure(self, module, message):
    """记录失败信息并保留已发布快照标识。"""
    with self.lock:
      state = self.readState(module)
      self.writeState(module, {**state, "error": message, "lastAttemptAt": currentTime()})
