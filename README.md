# Quant Forge

Quant Forge 是面向 A 股的行情、研究与决策辅助项目。
当前已实现盘前规则引擎、真实行情工作台和离线研究工具；新需求中的完整 AI 决策流程处于设计阶段。

## 需求与文档入口

- [新需求分析](新需求分析.md)：完整产品目标及原始待定事项。
- [需求迁移分析](docs/research/requirementsMigration-20261018.md)：已确认范围、复用边界及改造建议。
- [首版设计草案](docs/superpowers/specs/2026-10-18-decision-assistant-design.md)：待审阅的最小闭环方案。
- [文档导航](docs/README.md)：运行说明、数据接入与历史研究入口。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `src/quant_forge/` | 规则引擎、行情适配、研究工具及本地网页 |
| `tests/` | Python 单元测试、前端单元测试及隔离浏览器测试 |
| `config/` | 版本化默认规则 |
| `data/examples/` | 盘前规则的示例输入 |
| `data/research/` | 新闻研究的冻结摘要与控制样本 |
| `docs/` | 当前设计、运行说明和历史研究记录 |
| `tools/diagnostics/` | 手动诊断及真实快照验收脚本 |
| `outputs/` | 本地行情、研究证据、报告和恢复备份，不提交 Git |

## 当前盘前规则能力

它在北京时间 `08:50` 前读取已经形成的数据，输出：

- 全市场短线环境：赚钱效应、修复或退潮；
- 市场风格：首板套利、高低切、接力、混合或全面退潮；
- A～D 开仓权限；
- 账户总仓位和单笔仓位限制；
- 外围市场与新闻风险闸门；
- 板块相对优先级及逐条规则证据。

## 当前边界

盘前规则引擎不实现分时买点，不连接券商，也不自动下单。
新增独立真实行情工作台，支持沪深股票浏览与成交额、板块筛选；不输出买入信号。
示例 CSV 仅用于验证程序和规则接口。

## 真实行情工作台

```powershell
python -m pip install -e ".[web,dev]"
python -m quant_forge.web.app
```

打开 <http://127.0.0.1:8765>，点击“刷新行情”采集并保存真实数据。
后端读取 `HITHINK_FINANCE_API_KEY` 环境变量；没有密钥仍可查看已有快照。
首次全市场采集可能较慢，页面会显示正在采集；各模块独立更新，失败保留旧数据。
详细字段口径、快照位置和恢复说明见 [真实行情工作台](docs/research/live-market-workbench.md)。

## 环境要求

- Python 3.11 或更高版本；
- 所有文本文件使用 UTF-8 无 BOM 和 LF 换行。

安装开发依赖：

```powershell
python -m pip install -e ".[web,dev]"
```

## 生成示例报告

```powershell
python -m quant_forge.cli run `
  --input-dir data/examples `
  --output-dir outputs/example `
  --report-date 2026-09-08 `
  --previous-trade-date 2026-09-07 `
  --publish
```

成功后生成：

- `outputs/example/pre-market-decision.json`：使用 `--publish` 发布的唯一正式结构化结论；
- `outputs/example/pre-market-decision.md`：使用 `--publish` 发布的唯一正式中文报告；
- `outputs/example/history/<报告日>/<运行摘要>/`：不可覆盖的历史运行副本。

## 输入文件

输入目录固定包含四个 CSV：

| 文件 | 内容 |
| --- | --- |
| `market-premium.csv` | 昨日打首板、二板、多板和昨日涨停表现 |
| `external-markets.csv` | 美股、A50 及其他外围资产表现 |
| `news-events.csv` | 截止时间前完成结构化的新闻事件 |
| `sectors.csv` | 板块强度、广度、成交、持续性、催化和拥挤度 |

字段定义、阈值依据和安全降级规则见
[`docs/superpowers/specs/2026-09-08-market-environment-design.md`](docs/superpowers/specs/2026-09-08-market-environment-design.md)。

## 质量检查

```powershell
python -m ruff check .
python -m pytest
npm test
npm run test:browser
```

测试配置要求核心代码覆盖率不低于 `80%`。任何核心溢价字段缺失、日期异常或采集时间晚于截止时间时，系统必须输出禁止出手和零仓位。
前端验证依赖先通过 `npm ci` 安装；浏览器测试还需要可用的 Microsoft Edge。
手动诊断工具的输入、输出和执行条件见 [工具说明](tools/diagnostics/README.md)。

## 新闻分析小范围验证

新增独立的新闻提取与证据审查实验，保存三条真实历史材料和六条合成控制场景，
可离线回放模型分析记录并观察其对风险、仓位规则的假设影响。
这不是在线模型服务，也不是收益回测；不改变正式盘前结论。
运行方法与实测发现见 [新闻分析验证说明](docs/research/news-pilot.md)。
