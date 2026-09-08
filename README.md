# Quant Forge

Quant Forge 第一阶段是一个本地可运行、可解释、可回放的 A 股盘前环境与仓位决策规则引擎。

它在北京时间 `08:50` 前读取已经形成的数据，输出：

- 全市场短线环境：赚钱效应、修复或退潮；
- 市场风格：首板套利、高低切、接力、混合或全面退潮；
- A～D 开仓权限；
- 账户总仓位和单笔仓位限制；
- 外围市场与新闻风险闸门；
- 板块相对优先级及逐条规则证据。

## 当前边界

第一版不选择个股，不实现分时买点，不连接券商，也不自动下单。示例数据仅用于验证程序和规则接口，不代表真实市场结论或收益承诺。

## 环境要求

- Python 3.11 或更高版本；
- 所有文本文件使用 UTF-8 无 BOM 和 LF 换行。

安装开发依赖：

```powershell
python -m pip install -e ".[dev]"
```

## 生成示例报告

```powershell
python -m quant_forge.cli run `
  --input-dir data/examples `
  --output-dir outputs/example `
  --report-date 2026-09-08
```

成功后生成：

- `outputs/example/pre-market-decision.json`：供程序继续处理的结构化结论；
- `outputs/example/pre-market-decision.md`：供交易者盘前复核的中文报告。

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
```

测试配置要求核心代码覆盖率不低于 `80%`。任何核心溢价字段缺失、日期异常或采集时间晚于截止时间时，系统必须输出禁止出手和零仓位。
