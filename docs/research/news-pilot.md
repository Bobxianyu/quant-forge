# 新闻分析与证据审查：小范围验证

## 目的与实现

本实验验证 TradingAgents 的“先准备证据，再提取，再审查”模式是否适合 Quant Forge 的新闻入口。
现有正式规则和发布流程保持原样；新模块只调用风险、仓位函数做单事件敏感性分析。

两个分析角色本次均由当前 Codex 会话执行，分别保存提取与审查记录。
命令行程序执行的是这些记录的离线回放，不会调用模型 API，也没有部署常驻 Agent。
同一会话有共享上下文，不能视为独立审查；选材、摘要和分析也都来自本次会话，存在选择偏差。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `src/quant_forge/research/newsReview.py` | 校验候选、核对证据片段、时间和审查摘要绑定 |
| `src/quant_forge/research/newsPilot.py` | 单事件规则影响对比，输出 JSON 和中文报告 |
| `data/research/news-pilot/cases.json` | 三条真实材料摘要与六条合成控制材料 |
| `data/research/news-pilot/predictions.json` | 提取角色的固定输出；合成行是测试预置值 |
| `data/research/news-pilot/reviews.json` | 第二轮审查；绑定候选和冻结输入的 SHA-256 |
| `docs/research/news-analyst-prompt.md` | 提取角色提示词与字段契约 |
| `docs/research/news-reviewer-prompt.md` | 审查角色提示词与判断标准 |
| `tests/test_newsPilot.py` | 输入边界、风险规则影响、报告和样本回放测试 |

## 本地运行

在仓库根目录用 PowerShell 执行，显式指定当前源码，避免系统已安装包指向其他工作目录：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -X utf8 -m quant_forge.research.newsPilot `
  --input-dir data/research/news-pilot `
  --output-dir outputs/news-pilot
```

输出 `news-pilot.json` 和 `news-pilot.md`，不生成或覆盖 `pre-market-decision.*`。
重复运行会更新这两个实验报告；输入文件不变时 inputHash 和业务结论保持一致，回放耗时会变化。

```powershell
python -X utf8 -m pytest
python -m ruff check .
```

## 记录契约

- cases、predictions、reviews 均为 JSON 数组，标识不能重复；未知样本结果直接报错。
- 每条缺失的提取或审查仍计入样本分母，并进入 REVIEW。
- sentiment 为 -1 至 1，confidence 为 0 至 1，impactLevel 为 1 至 5 整数。
- sourceCount 不接受模型填写，由有效引文的 originId 去重计算。
- originId 由采集或人工整理端认定；当前不能自动判断两个来源是否真正独立。
- 引文必须存在于冻结文本；本次真实材料只冻结了标明出处的短摘要，并非完整原文归档。
- 发布时间 ≤ 采集时间 ≤ 提取完成时间 ≤ 审查完成时间 ≤ 实验截止时间。
- 本实验使用 48 小时的新鲜度上限，等于上限仍通过。此值是测试策略，不是生产校准结果。
- verdict 只有 accept 可通过；inputHash 与 candidateHash 必须对应当前输入和候选。
- 片段存在只能证明引用没有凭空捏造，不能证明情感、影响或板块推断正确。

真实材料的发布时间按页面记录。只精确到日期时，保守使用当日 23:59:59，并以
`publicationPrecision=day-conservative-end` 标记；该时刻不是声称的真实发布分钟。
采集与输出记录时间如实使用本次会话时间。真实案例的 cutoffAt 是本次阅读实验窗口终点，
不是交易日盘前截止时刻；合成案例使用明确虚构的 08:50 截止场景。

## 2026-09-12 的观察

| 真实材料 | 提取观察 | 审查与准入结果 |
| --- | --- | --- |
| 证券交易印花税减半公告 | 宏观正面信息；FIN 是影响传导推断 | 语义提取可接受，但旧闻不进入新增催化 |
| 中重稀土出口管制答记者问 | 监管事件，产业链影响方向不一 | 缺少稀土板块映射；不强行归类为全市场利空 |
| 央行三季度报告中的降准回顾 | 已实施的流动性支持措施 | 报告复述旧事件，不作为新增催化 |

在基础权限 C、没有外围价格风险的假设下，直接使用前两类正面标签中的
“印花税减半”和“历史降准回顾”都会得到 B；经过准入检查后，两者保持 C。
这说明只接入 sentiment 和 confidence 而忽略事件新鲜度，会通过现有规则产生权限提升。
该结果是规则敏感性演示，不是对这些历史交易日的还原。

九条记录的回放结果：三条可采纳、六条待核验。三条可采纳全部属于合成控制场景：

- 新鲜利好：C → B。
- 两个合成独立来源的重大负面事件：RED，C → D。
- 两个网址转载同一来源：来源数为 1，YELLOW，C → D。
- 伪造引文、超时输出和预置注入拒绝场景：不采纳，保持假设基线 C。

同源转载与独立证据虽然在基础 C 情况下都到 D，但风险级别不同，不能只比较最终权限。
NONE 仅代表没有已采纳的风险标签，不代表材料无风险或真实市场安全。

## 证据与验证记录

- TDD 第一轮：新增测试先报 `ModuleNotFoundError: quant_forge.research`，实现后 30 个测试通过。
- 第二轮：`test_changed_source_requires_new_review` 在输入未绑定时复现 ACCEPTED，
  加入输入摘要核验后转为 REVIEW。
- 最终新增 33 个测试用例，包含正常、边界、异常与整个固定样本的回放。
- 不计算模型准确率；没有独立人工金标准，模型调用耗时与费用不可得，报告对应字段为 null。
- 合成注入场景仅验证拒绝记录不会改变权限，并不证明模型可以抵御真实提示注入攻击。

## 下一步边界

1. 获取真正按时间归档的中文新闻原文，保存首发出处、实际采集时间、事件发生时间与更新关系。
2. 由人工独立标注并锁定样本，再让隔离上下文的两个模型角色生成输出。
3. 单独评估风险漏报/误报、板块映射、旧闻识别与证据支持率，记录实际模型成本和时延。
4. 完善板块字典；置信度经校准、来源独立性经验证后，再评估接入正式规则的条件。

现阶段没有足够证据允许 AI 新闻标签进入正式仓位权限决策。

## 原始来源

- [财政部证券交易印花税减半公告](https://www.mof.gov.cn/jrttts/202308/t20230828_3904235.htm)
- [商务部中重稀土出口管制答记者问](https://www.mofcom.gov.cn/xwfb/xwfyrth/art/2025/art_2eb722ef209c4d268dcd1aca8b2abf40.html)
- 央行《2024 年第三季度中国货币政策执行报告》内容摘要：完整 PDF 地址记录在 cases.json。

所有来源仅用于历史材料阅读，不用于判断这些政策的现行效力。
