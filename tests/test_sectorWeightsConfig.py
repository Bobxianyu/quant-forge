"""六项板块权重的输入边界回归测试，防止无效参数混入历史比较。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from quant_forge.config import load_decision_config, validate_decision_config
from quant_forge.rules.sector import rank_sectors

DEFAULT_PATH = Path("config/defaults.json")
WEIGHT_FIELDS = (
  ("relative_strength_weight", "relativeStrengthWeight"),
  ("breadth_weight", "breadthWeight"),
  ("limit_up_weight", "limitUpWeight"),
  ("turnover_weight", "turnoverWeight"),
  ("persistence_weight", "persistenceWeight"),
  ("catalyst_weight", "catalystWeight"),
)
WEIGHT_TOTAL = 0.90
NEGATIVE_WEIGHT = -0.10


def writeWeights(tmp_path, values):
  """从真实配置复制后替换六项权重，其他规则保持原样。"""
  raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
  raw["sector"].update(values)
  path = tmp_path / "weights.json"
  path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
  return path


@pytest.mark.parametrize("fieldIndex", range(len(WEIGHT_FIELDS)))
@pytest.mark.parametrize("entry", ["file", "object", "ranking"])
def testNegativeWeightIsRejectedEvenWhenTotalIsValid(tmp_path, fieldIndex, entry):
  """负权重由另一项抵消后仍必须拒绝，不能只检查合计。"""
  values = [0.0] * len(WEIGHT_FIELDS)
  values[fieldIndex] = NEGATIVE_WEIGHT
  values[(fieldIndex + 1) % len(values)] = WEIGHT_TOTAL - NEGATIVE_WEIGHT
  with pytest.raises(ValueError, match="权重.*不能为负数"):
    if entry == "file":
      path = writeWeights(tmp_path, {fields[1]: value for fields, value in zip(WEIGHT_FIELDS, values, strict=True)})
      load_decision_config(path)
    else:
      config = load_decision_config(DEFAULT_PATH)
      sector = replace(config.sector, **{
        fields[0]: value for fields, value in zip(WEIGHT_FIELDS, values, strict=True)
      })
      if entry == "object":
        validate_decision_config(replace(config, sector=sector))
      else:
        rank_sectors([], config=sector)


@pytest.mark.parametrize("fieldIndex", range(len(WEIGHT_FIELDS)))
@pytest.mark.parametrize("invalid", [False, "0"])
def testJsonWeightCannotSilentlyConvertBooleanOrText(tmp_path, fieldIndex, invalid):
  """布尔或文本不能先被 float 转换后伪装成合法权重。"""
  values = {fields[1]: 0.0 for fields in WEIGHT_FIELDS}
  fieldName = WEIGHT_FIELDS[fieldIndex][1]
  values[fieldName] = invalid
  values[WEIGHT_FIELDS[(fieldIndex + 1) % len(WEIGHT_FIELDS)][1]] = WEIGHT_TOTAL
  path = writeWeights(tmp_path, values)
  with pytest.raises(ValueError, match=rf"{fieldName}.*数字"):
    load_decision_config(path)


@pytest.mark.parametrize("entry", ["file", "object", "ranking"])
def testHugeIntegerWeightProducesClearValidationError(tmp_path, entry):
  """超出浮点范围的整数应报告中文配置错误，不能泄漏 OverflowError。"""
  huge = 10 ** 400
  with pytest.raises(ValueError, match="权重.*有限数"):
    if entry == "file":
      load_decision_config(writeWeights(tmp_path, {"relativeStrengthWeight": huge}))
    else:
      config = load_decision_config(DEFAULT_PATH)
      sector = replace(config.sector, relative_strength_weight=huge)
      if entry == "object":
        validate_decision_config(replace(config, sector=sector))
      else:
        rank_sectors([], config=sector)


@pytest.mark.parametrize("fieldIndex", range(len(WEIGHT_FIELDS)))
def testZeroWeightsAndOneFullWeightRemainValid(tmp_path, fieldIndex):
  """允许关闭单项贡献，非负边界与现有 0.90 总和语义保持兼容。"""
  values = {fields[1]: 0 for fields in WEIGHT_FIELDS}
  values[WEIGHT_FIELDS[fieldIndex][1]] = WEIGHT_TOTAL
  config = load_decision_config(writeWeights(tmp_path, values))
  validate_decision_config(config)
  assert getattr(config.sector, WEIGHT_FIELDS[fieldIndex][0]) == WEIGHT_TOTAL


@pytest.mark.parametrize("total", [0.0, 0.89, 0.91, 1.0])
def testIncorrectWeightTotalRemainsRejected(tmp_path, total):
  """新增逐项约束不能放松原有基础权重总和要求。"""
  values = {fields[1]: 0.0 for fields in WEIGHT_FIELDS}
  values["relativeStrengthWeight"] = total
  with pytest.raises(ValueError, match="权重之和必须为 0.90"):
    load_decision_config(writeWeights(tmp_path, values))


@pytest.mark.parametrize("invalid", [False, "0.25", None, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("entry", ["object", "ranking"])
def testMalformedDirectWeightsAreRejectedBeforeScoring(invalid, entry):
  """公开评分入口和完整配置入口使用一致的数值约束，空列表也不能绕过。"""
  config = load_decision_config(DEFAULT_PATH)
  sector = replace(config.sector, relative_strength_weight=invalid)
  with pytest.raises(ValueError):
    if entry == "object":
      validate_decision_config(replace(config, sector=sector))
    else:
      rank_sectors([], config=sector)


def testTinyNegativeWeightCannotUseSumTolerance():
  """总和容差只处理加法精度，不容许任何负权重。"""
  config = load_decision_config(DEFAULT_PATH)
  values = {fields[0]: 0.0 for fields in WEIGHT_FIELDS}
  values.update(relative_strength_weight=-1e-12, breadth_weight=WEIGHT_TOTAL)
  with pytest.raises(ValueError, match="不能为负数"):
    rank_sectors([], config=replace(config.sector, **values))


def testOrdinaryFloatingSumDifferenceRemainsValid(tmp_path):
  """合法的浮点相加误差不应使配置被拒绝。"""
  values = {fields[1]: 0.0 for fields in WEIGHT_FIELDS}
  values.update({fields[1]: 0.1 + 0.2 for fields in WEIGHT_FIELDS[:3]})
  config = load_decision_config(writeWeights(tmp_path, values))
  assert rank_sectors([], config=config.sector) == ()
