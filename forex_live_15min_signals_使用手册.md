# 15分钟外汇策略实时监控 / 回测 — 使用手册

基于 **forex_backtest_15min_model** 的多周期趋势 + 突破回调策略：拉取指定品种的 15 分钟 K 线，检测到交易机会时通过**企业微信机器人**推送通知，并在本地保存**建议时刻的 K 线图**与**每次扫描的品种快照**。同时支持**回测模式**：对指定品种在指定时间段内进行历史回测。

---

## 一、功能说明

| 功能 | 说明 |
|------|------|
| 数据来源 | Twelve Data API，拉取指定品种的**最新 300 根 15 分钟 K 线**（实时）或**指定日期区间的 15 分钟 K 线**（回测） |
| 策略逻辑 | 与 `forex_backtest_15min_model` 一致：1h+15m 多周期趋势同向（EMA20/50/100）、前 20 根突破、回踩 EMA20、反转/趋势 K 触发、结构止损 + ATR 兜底、分批止盈 |
| 信号筛选 | 实时模式：仅把**最近 N 根 K 线内**出现的信号视为「当前机会」（默认 N=5），避免重复推送 |
| 回测模式 | 对指定品种在 `--start-date`～`--end-date` 内跑完整回测，输出交易数、胜率、盈亏比、最大回撤、夏普、收益率及每笔交易 K 线图 |
| 通知内容 | 品种、趋势方向（做多/做空）、建议入场价、止损价、止盈 TP1、止盈 TP2、信号时间 |
| 图表存档 | 实时：`live_15m_signals/时间戳/`（含品种快照 + 有信号时的建议图）；回测：`backtest_outputs/15min_live_时间戳/trade_charts/` |

---

## 二、使用前准备

### 2.1 环境要求

- Python 3.7+
- 依赖：`requests`、`pandas`、`matplotlib`（与 `forex_backtest_15min_model` 相同）

### 2.2 Twelve Data API Key

1. 打开 [Twelve Data](https://twelvedata.com) 注册并获取 API Key。
2. 任选一种方式配置：
   - **推荐**：设置环境变量 `TWELVEDATA_API_KEY=你的Key`
   - 或直接修改 `forex_live_15min_signals.py` 顶部 `TWELVEDATA_API_KEY = "你的Key"`

### 2.3 企业微信机器人（可选）

不配置则只会在控制台打印建议，不发送企业微信。

1. 在企业微信中创建一个群，进入 **群设置 → 群机器人 → 添加机器人**。
2. 复制生成的 **Webhook 地址**（形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx`）。
3. 任选一种方式配置：
   - **推荐**：设置环境变量 `WECOM_WEBHOOK_URL=你的Webhook地址`
   - 或修改脚本顶部 `WECOM_WEBHOOK_URL = "你的Webhook地址"`

---

## 三、支持的品种

| 脚本中的名称 | Twelve Data 对应 |
|-------------|------------------|
| EURUSD      | EUR/USD          |
| USDJPY      | USD/JPY          |
| AUDUSD      | AUD/USD          |
| XAUUSD      | XAU/USD          |
| GBPUSD      | GBP/USD          |

**实时监控**默认品种在脚本顶部 `SYMBOLS` 中配置（当前为上述五品种）。  
**回测模式**可通过参数 `--symbol` 指定要回测的品种，不指定则使用 `SYMBOLS` 中的全部品种。

---

## 四、运行方式

在项目目录下执行（PowerShell 或 CMD）：

```bash
python forex_live_15min_signals.py [选项]
```

### 4.1 常用命令

| 用途 | 命令示例 |
|------|----------|
| 只跑一次扫描（默认品种） | `python forex_live_15min_signals.py --once` |
| 持续监控（每 5 分钟扫描一次） | `python forex_live_15min_signals.py` |
| **回测**：指定时间段、默认全部品种 | `python forex_live_15min_signals.py --backtest --start-date 2024-01-01 --end-date 2024-06-01` |
| **回测**：指定品种 | `python forex_live_15min_signals.py --backtest --symbol EURUSD --symbol XAUUSD --start-date 2024-01-01` |
| **回测**：不保存每笔交易 K 线图 | `python forex_live_15min_signals.py --backtest --start-date 2024-01-01 --end-date 2024-06-01 --no-charts` |

### 4.2 参数说明

**实时模式：**

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--once` | 只执行一次扫描后退出 | 不加则按间隔循环运行 |

实时监控的品种、扫描间隔、拉取 K 线根数等均在脚本顶部「配置区」修改（如 `SYMBOLS`、`SCAN_INTERVAL_SECONDS`、`LIVE_BARS`、`SIGNAL_FRESH_BARS`）。

**回测模式（与 `--backtest` 同时使用）：**

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--backtest` | 启用回测模式（不再跑实时监控） | — |
| `--start-date` | 回测开始日期，格式 `YYYY-MM-DD` | 约 220 天前 |
| `--end-date` | 回测结束日期，格式 `YYYY-MM-DD` | 当天 |
| `--symbol` | 回测品种，可多次指定（如 `--symbol EURUSD --symbol XAUUSD`） | 使用配置中全部品种 |
| `--no-charts` | 回测时不保存每笔交易的 K 线图 | 默认保存 |

### 4.3 示例

```bash
# 只扫描一次默认五品种
python forex_live_15min_signals.py --once

# 持续监控，每 5 分钟扫描（间隔在脚本内 SCAN_INTERVAL_SECONDS=300）
python forex_live_15min_signals.py

# 回测：2024-01-01 至 2024-06-01，默认全部品种
python forex_live_15min_signals.py --backtest --start-date 2024-01-01 --end-date 2024-06-01

# 回测：仅欧元与黄金
python forex_live_15min_signals.py --backtest --symbol EURUSD --symbol XAUUSD --start-date 2024-01-01 --end-date 2024-12-01

# 回测：不保存 K 线图，仅看统计
python forex_live_15min_signals.py --backtest --start-date 2024-01-01 --end-date 2024-06-01 --no-charts
```

---

## 五、输出说明

### 5.1 控制台

- 实时模式：每次扫描会打印时间、各品种是否保存快照、是否有新信号及推送结果。
- 回测模式：每个品种打印信号数、交易数、胜率、盈亏比(PF)、最大回撤、夏普、收益率；最后有汇总表。

### 5.2 企业微信消息格式（Markdown）

- **品种**、**趋势方向**（做多/做空）
- **建议买入价格**、**止损价格**、**止盈 TP1**、**止盈 TP2**
- **信号时间**、**推送时间**
- 说明：基于最近 300 根 15 分钟 K 线，多周期趋势+突破回调策略

### 5.3 实时模式：文件与图表

- **根目录**：`live_15m_signals/`
- **每次扫描**一个子目录：`live_15m_signals/YYYYMMDD_HHMMSS/`
- 每个品种会保存一张**监控快照**：`{品种}_snapshot_{时间}.png`（含 K 线、EMA20/50/100）
- 若有信号并推送，会额外保存**建议图**：`{品种}_{long|short}_{时间}.png`（含入场、止损、TP1、TP2 标注）
- **已发送信号缓存**：`live_15m_sent_signals.json`，用于避免同一机会重复推送（保留最近 7 天）

### 5.4 回测模式：输出说明

- **控制台**：每个品种的信号数、交易数、胜率、PF、最大回撤、夏普、收益率；汇总表。
- **输出根目录**：`backtest_outputs/15min_live_YYYYMMDD_HHMMSS/`
- **每笔交易 K 线图**（未加 `--no-charts` 时）：`backtest_outputs/15min_live_时间戳/trade_charts/{品种}/`，按交易顺序命名（如 `1.png`、`2.png`）

---

## 六、在代码中调用

**回测指定时间段：**

```python
from forex_live_15min_signals import run_backtest_mode, TWELVEDATA_API_KEY

results = run_backtest_mode(
    symbols=["EURUSD", "XAUUSD"],
    start_date="2024-01-01",
    end_date="2024-06-01",
    api_key=TWELVEDATA_API_KEY,
    save_charts=True,
)
# results 为列表，每项为 {"symbol": "EURUSD", "result": { "total_trades", "win_rate", "profit_factor", "max_drawdown", "sharpe", "final_capital", "trades", "chart_files", ... }}
```

**单次扫描（实时逻辑）** 可通过直接调用 `scan_once(load_sent_signals())` 实现，需自行准备 `sent_signals` 的持久化；通常更推荐直接运行脚本的 `--once` 或循环模式。

---

## 七、常见问题

**Q：提示「请在环境变量或脚本顶部配置 TWELVEDATA_API_KEY」**  
A：按「二、使用前准备」配置 API Key（环境变量或脚本内默认值）。

**Q：没有收到企业微信消息**  
A：检查 `WECOM_WEBHOOK_URL` 是否配置正确；控制台是否出现发送成功提示。若返回 errcode，可对照企业微信文档排查 Webhook 与消息格式。

**Q：某个品种报错或跳过**  
A：可能是该品种暂无足够数据（至少需 300 根 15m K 线）或 API 限流。脚本会继续处理其他品种；可稍后重试或减少品种数量。

**Q：想修改监控品种或扫描间隔**  
A：在 `forex_live_15min_signals.py` 顶部配置区修改 `SYMBOLS`、`SCAN_INTERVAL_SECONDS` 等常量。回测品种可用命令行 `--symbol` 指定。

**Q：回测的默认时间范围是什么？**  
A：不指定时，`--end-date` 为当天，`--start-date` 为约 220 天前。建议用 `--start-date` 和 `--end-date` 显式指定区间。

**Q：回测用的数据和实时一样吗？**  
A：是。回测使用同一套 15 分钟 K 线与 `forex_backtest_15min_model` 策略逻辑，仅数据时间范围由 `--start-date` / `--end-date` 指定，通过 Twelve Data 按区间拉取。

**Q：如何长时间后台运行？**  
A：可用系统计划任务定时执行 `--once`；或在服务器上用 `nohup`/`screen` 运行不带 `--once` 的循环，并配合脚本内的 `SCAN_INTERVAL_SECONDS`。

---

## 八、文件与依赖关系

- **forex_live_15min_signals.py**：本程序主脚本；依赖 **forex_backtest_15min_model.py** 中的 `Forex15MinBacktester`、`StrategyConfig`；支持实时监控与回测两种模式。
- **forex_backtest_15min_model.py**：提供 15 分钟多周期趋势+突破回调策略的信号与回测逻辑，无需单独运行。
- **live_15m_signals/**：由本程序自动创建，存放实时模式下每次扫描的快照与有信号时的建议 K 线图。
- **live_15m_sent_signals.json**：已发送信号缓存，避免重复推送。
- **backtest_outputs/**：回测模式下自动创建，其下 `15min_live_时间戳/trade_charts/` 存放每笔回测交易的 K 线图。

---

*使用手册对应脚本：`forex_live_15min_signals.py`*
