# A 股盘前环境与仓位决策系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个可从标准 CSV 快照生成市场环境、市场风格、开仓权限、双层仓位、风险闸门和板块排名的本地 Python 规则引擎。

**Architecture:** 使用 `src` 布局和不可变领域对象隔离数据、规则、编排与展示。四个短线溢价指标只由环境和权限规则消费，外围与新闻只通过风险闸门调整权限，板块评分独立计算；所有结果携带规则证据并通过结构化报告汇总。

**Tech Stack:** Python 3.11+、标准库 `dataclasses`/`enum`/`csv`/`json`/`datetime`、pytest、pytest-cov、Ruff。

---

## 文件结构

```text
quant-forge/
├── .gitattributes
├── .gitignore
├── pyproject.toml
├── README.md
├── config/defaults.json
├── data/examples/
│   ├── market-premium.csv
│   ├── external-markets.csv
│   ├── news-events.csv
│   └── sectors.csv
├── src/quant_forge/
│   ├── __init__.py
│   ├── cli.py
│   ├── pipeline.py
│   ├── adapters/csv_adapter.py
│   ├── domain/models.py
│   ├── reporting/markdown_report.py
│   └── rules/
│       ├── environment.py
│       ├── position.py
│       ├── risk.py
│       └── sector.py
└── tests/
    ├── adapters/test_csv_adapter.py
    ├── reporting/test_markdown_report.py
    ├── rules/test_environment.py
    ├── rules/test_position.py
    ├── rules/test_risk.py
    ├── rules/test_sector.py
    └── test_pipeline.py
```

### Task 1: 建立可验证的 Python 项目基线

**Files:**
- Create: `.gitattributes`
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/quant_forge/__init__.py`

- [ ] **Step 1: 写入换行、忽略项和包元数据**

```gitattributes
* text=auto eol=lf
*.py text eol=lf
*.md text eol=lf
*.json text eol=lf
*.csv text eol=lf
```

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "quant-forge"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.3,<9", "pytest-cov>=6,<7", "ruff>=0.9,<1"]

[project.scripts]
quant-forge = "quant_forge.cli:main"

[tool.pytest.ini_options]
addopts = "-q --cov=quant_forge --cov-report=term-missing --cov-fail-under=80"
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 120
target-version = "py311"
```

- [ ] **Step 2: 验证项目元数据可解析**

Run: `python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb')); print('配置有效')"`
Expected: 输出 `配置有效`。

- [ ] **Step 3: 提交项目基线**

```bash
git add .gitattributes .gitignore pyproject.toml src/quant_forge/__init__.py
git commit -m "chore(项目): 初始化 Python 工程基线"
```

### Task 2: 定义领域对象和枚举

**Files:**
- Create: `tests/test_models.py`
- Create: `src/quant_forge/domain/models.py`
- Create: `src/quant_forge/domain/__init__.py`

- [ ] **Step 1: 编写失败测试，固定四项溢价快照接口**

```python
from datetime import date, datetime

from quant_forge.domain.models import MarketPremiumSnapshot


def test_market_premium_snapshot_keeps_four_premium_metrics() -> None:
  snapshot = MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=2.1,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="example",
    collected_at=datetime(2026, 9, 8, 8, 30),
  )

  assert snapshot.first_board_premium_pct == 2.1
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run: `python -m pytest tests/test_models.py -q --no-cov`
Expected: FAIL，错误包含 `No module named 'quant_forge.domain'`。

- [ ] **Step 3: 实现不可变领域对象**

`models.py` 必须定义 `MarketEnvironment`、`RecoveryStrength`、`MarketStyle`、`PermissionGrade`、`Action`、`RiskLevel`、`SectorTrend` 枚举，以及 `MarketPremiumSnapshot`、`ExternalMarketSnapshot`、`NewsEvent`、`SectorSnapshot`、`RuleEvidence`、`EnvironmentAssessment`、`RiskAssessment`、`PositionRange`、`PositionDecision`、`SectorOutlook`、`PreMarketDecision` 不可变数据类。

- [ ] **Step 4: 运行领域对象测试**

Run: `python -m pytest tests/test_models.py -q --no-cov`
Expected: PASS。

- [ ] **Step 5: 提交领域对象**

```bash
git add src/quant_forge/domain tests/test_models.py
git commit -m "feat(领域模型): 定义盘前决策数据对象"
```

### Task 3: 实现环境、风格和基础权限规则

**Files:**
- Create: `tests/rules/test_environment.py`
- Create: `src/quant_forge/rules/environment.py`
- Create: `src/quant_forge/rules/__init__.py`

- [ ] **Step 1: 编写退潮、赚钱效应、修复和边界失败测试**

```python
def test_classifies_ebb_tide_when_all_metrics_are_negative(snapshot_factory) -> None:
  result = assess_environment(snapshot_factory(-0.1, -0.2, -0.3, -0.4))
  assert result.environment is MarketEnvironment.EBB_TIDE
  assert result.permission_grade is PermissionGrade.D


def test_classifies_profit_effect_when_three_metrics_are_positive(snapshot_factory) -> None:
  result = assess_environment(snapshot_factory(2.5, 1.0, 0.5, 2.2))
  assert result.environment is MarketEnvironment.PROFIT_EFFECT
  assert result.permission_grade is PermissionGrade.A


def test_treats_zero_as_neutral_and_returns_weak_recovery(snapshot_factory) -> None:
  result = assess_environment(snapshot_factory(0.0, 0.0, -0.1, 0.0))
  assert result.environment is MarketEnvironment.RECOVERY
  assert result.recovery_strength is RecoveryStrength.WEAK
```

- [ ] **Step 2: 运行测试并确认函数缺失**

Run: `python -m pytest tests/rules/test_environment.py -q --no-cov`
Expected: FAIL，错误指向 `assess_environment` 不存在。

- [ ] **Step 3: 按规格优先级实现 `assess_environment`**

实现顺序必须为全面退潮、环境分类、修复强弱、风格分类和 A～D 基础权限。返回证据应包含 `F/S/M/L` 实际值、命中规则编号和阈值。

- [ ] **Step 4: 运行规则测试**

Run: `python -m pytest tests/rules/test_environment.py -q --no-cov`
Expected: PASS。

- [ ] **Step 5: 提交环境规则**

```bash
git add src/quant_forge/rules tests/rules/test_environment.py
git commit -m "feat(环境分析): 实现市场环境与权限规则"
```

### Task 4: 实现风险闸门和双层仓位

**Files:**
- Create: `tests/rules/test_risk.py`
- Create: `tests/rules/test_position.py`
- Create: `src/quant_forge/rules/risk.py`
- Create: `src/quant_forge/rules/position.py`
- Create: `config/defaults.json`

- [ ] **Step 1: 编写红色和橙色闸门失败测试**

```python
def test_red_risk_forces_zero_position(major_negative_news) -> None:
  result = assess_risk([], [major_negative_news])
  assert result.level is RiskLevel.RED


def test_nasdaq_drop_at_three_percent_triggers_orange(nasdaq_snapshot_factory) -> None:
  result = assess_risk([nasdaq_snapshot_factory(-3.0)], [])
  assert result.level is RiskLevel.ORANGE
```

- [ ] **Step 2: 编写仓位封顶失败测试**

```python
def test_orange_risk_caps_grade_a_position_at_grade_c() -> None:
  result = decide_position(PermissionGrade.A, RiskLevel.ORANGE)
  assert result.final_grade is PermissionGrade.C
  assert result.total.maximum == 20
  assert result.single.maximum == 10
```

- [ ] **Step 3: 运行测试并确认规则函数缺失**

Run: `python -m pytest tests/rules/test_risk.py tests/rules/test_position.py -q --no-cov`
Expected: FAIL，错误分别指向 `assess_risk` 和 `decide_position`。

- [ ] **Step 4: 实现风险优先级和仓位映射**

风险判断按红、橙、黄、无风险的顺序短路；仓位映射严格采用 A=`50～80/20～30`、B=`20～50/10～20`、C=`0～20/0～10`、D=`0/0`，其中斜线前后分别为总仓位和单笔仓位。

- [ ] **Step 5: 运行风险和仓位测试**

Run: `python -m pytest tests/rules/test_risk.py tests/rules/test_position.py -q --no-cov`
Expected: PASS。

- [ ] **Step 6: 提交风险和仓位规则**

```bash
git add config/defaults.json src/quant_forge/rules tests/rules/test_risk.py tests/rules/test_position.py
git commit -m "feat(风险控制): 实现风险闸门与双层仓位"
```

### Task 5: 实现板块评分和排名

**Files:**
- Create: `tests/rules/test_sector.py`
- Create: `src/quant_forge/rules/sector.py`

- [ ] **Step 1: 编写评分顺序和退潮测试**

```python
def test_stronger_sector_ranks_first(strong_sector, weak_sector) -> None:
  result = rank_sectors([weak_sector, strong_sector], limit=3)
  assert result[0].sector_code == strong_sector.sector_code


def test_crowded_sector_with_weak_breadth_is_possible_ebb_tide(crowded_sector) -> None:
  result = rank_sectors([crowded_sector], limit=3)
  assert result[0].trend is SectorTrend.POSSIBLE_EBB_TIDE
```

- [ ] **Step 2: 运行测试并确认排名函数缺失**

Run: `python -m pytest tests/rules/test_sector.py -q --no-cov`
Expected: FAIL，错误指向 `rank_sectors` 不存在。

- [ ] **Step 3: 实现归一化、加权评分和稳定排序**

相对强度按 `-5%～5%` 映射到 `0～100`，涨停数按 `0～10` 映射，成交变化按 `-50%～50%` 映射，持续天数按 `0～5` 映射；其他字段已为 `0～100`。同分时按 `sector_code` 升序，保证结果可复现。

- [ ] **Step 4: 运行板块测试**

Run: `python -m pytest tests/rules/test_sector.py -q --no-cov`
Expected: PASS。

- [ ] **Step 5: 提交板块规则**

```bash
git add src/quant_forge/rules/sector.py tests/rules/test_sector.py
git commit -m "feat(板块轮动): 实现板块评分与排名"
```

### Task 6: 实现 CSV 适配和数据质量校验

**Files:**
- Create: `tests/adapters/test_csv_adapter.py`
- Create: `src/quant_forge/adapters/__init__.py`
- Create: `src/quant_forge/adapters/csv_adapter.py`
- Create: `data/examples/market-premium.csv`
- Create: `data/examples/external-markets.csv`
- Create: `data/examples/news-events.csv`
- Create: `data/examples/sectors.csv`

- [ ] **Step 1: 编写合法快照和核心字段缺失测试**

```python
def test_loads_four_core_premium_metrics(tmp_path) -> None:
  path = tmp_path / "market.csv"
  path.write_text(
    "tradeDate,firstBoardPremiumPct,secondBoardPremiumPct,multiBoardPremiumPct,limitUpPremiumPct,source,collectedAt\n"
    "2026-09-07,2.1,1.0,0.5,2.2,example,2026-09-08T08:30:00\n",
    encoding="utf-8",
  )
  assert load_market_premium(path).limit_up_premium_pct == 2.2


def test_missing_core_field_raises_chinese_error(tmp_path) -> None:
  path = tmp_path / "market.csv"
  path.write_text("tradeDate,source\n2026-09-07,example\n", encoding="utf-8")
  with pytest.raises(DataValidationError, match="缺少核心字段"):
    load_market_premium(path)
```

- [ ] **Step 2: 运行测试并确认适配器缺失**

Run: `python -m pytest tests/adapters/test_csv_adapter.py -q --no-cov`
Expected: FAIL，错误指向 `csv_adapter` 不存在。

- [ ] **Step 3: 实现四类 CSV 读取函数和中文校验错误**

必须实现 `load_market_premium`、`load_external_markets`、`load_news_events` 和 `load_sectors`。禁止使用空 `except`；错误信息包含文件、行号和具体字段。

- [ ] **Step 4: 运行适配器测试**

Run: `python -m pytest tests/adapters/test_csv_adapter.py -q --no-cov`
Expected: PASS。

- [ ] **Step 5: 提交适配器和示例数据**

```bash
git add src/quant_forge/adapters tests/adapters data/examples
git commit -m "feat(数据接入): 新增 CSV 数据契约与校验"
```

### Task 7: 编排完整决策并生成报告

**Files:**
- Create: `tests/test_pipeline.py`
- Create: `tests/reporting/test_markdown_report.py`
- Create: `src/quant_forge/pipeline.py`
- Create: `src/quant_forge/reporting/__init__.py`
- Create: `src/quant_forge/reporting/markdown_report.py`

- [ ] **Step 1: 编写正常报告和核心数据过期失败测试**

```python
def test_pipeline_outputs_environment_position_risk_and_sectors(valid_inputs) -> None:
  decision = build_pre_market_decision(**valid_inputs)
  assert decision.environment is MarketEnvironment.PROFIT_EFFECT
  assert decision.position.final_grade is PermissionGrade.A
  assert len(decision.sector_outlooks) <= 3


def test_pipeline_degrades_safely_for_stale_core_data(stale_inputs) -> None:
  decision = build_pre_market_decision(**stale_inputs)
  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.position.total.maximum == 0
```

- [ ] **Step 2: 运行测试并确认编排函数缺失**

Run: `python -m pytest tests/test_pipeline.py tests/reporting/test_markdown_report.py -q --no-cov`
Expected: FAIL，错误指向管道或报告模块不存在。

- [ ] **Step 3: 实现管道、JSON 序列化和中文 Markdown 报告**

管道必须先校验核心快照，再依次调用环境、风险、仓位和板块模块。Markdown 报告必须展示截止时间、环境、风格、权限、仓位、风险、板块前三、正负因素、缺失字段和规则版本。

- [ ] **Step 4: 运行管道和报告测试**

Run: `python -m pytest tests/test_pipeline.py tests/reporting/test_markdown_report.py -q --no-cov`
Expected: PASS。

- [ ] **Step 5: 提交决策管道**

```bash
git add src/quant_forge/pipeline.py src/quant_forge/reporting tests/test_pipeline.py tests/reporting
git commit -m "feat(盘前报告): 编排决策并生成可解释报告"
```

### Task 8: 提供本地命令和使用文档

**Files:**
- Create: `src/quant_forge/cli.py`
- Create: `tests/test_cli.py`
- Create: `README.md`

- [ ] **Step 1: 编写命令行端到端失败测试**

```python
def test_cli_generates_json_and_markdown_reports(tmp_path) -> None:
  exit_code = main(["run", "--input-dir", "data/examples", "--output-dir", str(tmp_path)])
  assert exit_code == 0
  assert (tmp_path / "pre-market-decision.json").exists()
  assert (tmp_path / "pre-market-decision.md").exists()
```

- [ ] **Step 2: 运行测试并确认命令缺失**

Run: `python -m pytest tests/test_cli.py -q --no-cov`
Expected: FAIL，错误指向 `main` 不存在。

- [ ] **Step 3: 实现 `run` 命令和中文使用说明**

命令必须接受 `--input-dir`、`--output-dir`、`--report-date` 和 `--cutoff-time`。失败时向标准错误输出中文原因并返回非零状态，不打印堆栈中的敏感路径或令牌。

- [ ] **Step 4: 运行端到端测试**

Run: `python -m pytest tests/test_cli.py -q --no-cov`
Expected: PASS，并在临时目录生成 JSON 和 Markdown。

- [ ] **Step 5: 提交命令和文档**

```bash
git add src/quant_forge/cli.py tests/test_cli.py README.md
git commit -m "feat(命令行): 提供盘前报告生成入口"
```

### Task 9: 完成全量质量门禁

**Files:**
- Modify: only files required to fix failures found by the commands below

- [ ] **Step 1: 运行格式与静态检查**

Run: `python -m ruff check .`
Expected: 输出 `All checks passed!`。

- [ ] **Step 2: 运行全量测试和覆盖率门禁**

Run: `python -m pytest`
Expected: 所有测试通过，核心覆盖率不低于 `80%`。

- [ ] **Step 3: 验证示例命令**

Run: `python -m quant_forge.cli run --input-dir data/examples --output-dir outputs/example`
Expected: 退出码为 `0`，生成结构化 JSON 和中文 Markdown 报告。

- [ ] **Step 4: 检查未来数据和安全降级证据**

Run: `python -m pytest tests/test_pipeline.py -q -k "截止时间 or 过期 or 缺失"`
Expected: 相关测试全部通过。

- [ ] **Step 5: 确认工作区没有遗漏修改**

Run: `git status --short`
Expected: 无输出。质量门禁发现的错误必须回到对应任务修复、复测并使用该任务规定的提交信息提交，禁止创建空提交。
