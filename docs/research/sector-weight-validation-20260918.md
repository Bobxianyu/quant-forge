# 六项板块权重的配置校验修复

日期：2026-09-18。范围：权重参数合法性，不调整权重数值、不拟合新策略。

## 一、问题与修复

原校验只要求六项基础权重合计为 0.90。
例如相对强度为 -0.10、板块广度为 1.00、其余为 0，合计仍是 0.90，旧逻辑会接受。
这会将本应正向贡献的强度项变成负向贡献，让无效组合进入评分或未来权重搜索。

另有两个入口问题：JSON 中的 false 和数字字符串会先被转换为浮点数，原始类型错误被隐藏；
直接向板块排序传入配置对象时，没有经过完整配置校验，空板块列表也会直接返回成功。
超出浮点范围的整数还会泄漏 OverflowError，无法得到预期的中文配置错误。

本次统一为以下契约：

- 六项基础权重必须是整数或浮点数；拒绝布尔、字符串、空值及其他类型。
- 每一项必须有限且非负；允许整数 0、浮点数 0 和关闭部分评分项。
- 六项合计必须为 0.90，总和保留原有 0.000000001 的绝对容差。
- 总和容差不适用于负数；即使只有 -0.000000000001，也会被拒绝。
- 一项为 0.90、其余为 0 的边界组合仍可通过合法性校验，但这不代表该组合投资效果更好。
- 拥挤度继续单独调整分数，不作为第七个基础权重，也不参与 0.90 合计。

## 二、代码入口

[config.py](../../src/quant_forge/config.py) 新增公开函数
`validateSectorWeights(config: SectorRuleConfig) -> None`：
逐项校验六权重并检查总和，失败抛出中文 ValueError，不修改调用方配置。

内部 `_weightNumber` 在转换前检查原始类型，处理超大整数、非有限值和负数；
`_sector_config` 在读取六项 JSON 权重时使用它。
`validate_decision_config` 在执行后续完整配置检查前调用共享权重校验。

[rules/sector.py](../../src/quant_forge/rules/sector.py) 的 `rank_sectors`
在遍历板块数据之前调用同一校验。直接注入配置和空列表均不能绕过。

示例：

```python
from dataclasses import replace
from quant_forge.config import load_default_config, validateSectorWeights

weights = load_default_config().sector
validateSectorWeights(weights)

# 非法负权重会立即抛出中文 ValueError，不会自动改成零或归一化。
validateSectorWeights(replace(weights, relative_strength_weight=-0.1, breadth_weight=0.5))
```

## 三、测试与兼容范围

新增 [test_sectorWeightsConfig.py](../../tests/test_sectorWeightsConfig.py) 的 57 项用例。
修复前有 40 项失败、17 项通过；修复后新增用例和既有相关用例全部通过。
覆盖六字段负值、三个调用入口、布尔和文本转换、非有限值、超大整数、零边界、错误总和及浮点容差。

全量测试 **261 项通过**，整体覆盖率 **91.27%**；相关文件静态检查通过。
默认六权重、配置内容标识、评分公式、拥挤度调整、截断、保留两位小数和排序方式均保持不变。

兼容性变化仅影响非法配置：以前被隐式转换的文本或布尔权重、负数权重，现在会明确报错。
自定义配置文件应使用 JSON 数字，例如 0.25，而不是字符串 "0.25"。

## 四、历史研究状态

此次修复为权重比较建立了参数边界，不证明默认权重最优，也没有消除历史数据缺口。
完整任务状态与后续研究路线见 [续作记录](weight-task-progress.md)。
