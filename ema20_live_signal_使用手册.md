# EMA20 实时交易机会检测 — 使用手册

基于 **EMA20 回调策略**，用最新 200 根 15 分钟 K 线检测当前是否有交易机会；有机会时通过**企业微信机器人**推送通知，并在本地保存**建议时刻的 K 线图**。同时支持**回测模式**：对指定品种在指定时间段内进行历史回测。

---

## 一、功能说明

| 功能 | 说明 |
|------|------|
| 数据来源 | TwelveData API，拉取指定外汇/贵金属品种的**最新 200 根 15 分钟 K 线**（实时）或**指定日期区间的 15 分钟 K 线**（回测） |
| 策略逻辑 | 与 `EMA20回调策略.py` 一致：多周期趋势（EMA20>EMA50>EMA100）、突破前 20 根高低点、回踩 EMA20、反转/趋势 K 线确认 |
| 信号筛选 | 实时模式：仅把**最近 N 根 K 线内**出现的信号视为「当前机会」（默认 N=5），避免推送历史信号 |
| 回测模式 | 对指定品种在 `--start`～`--end` 时间段内跑完整回测，输出交易次数、胜率、总盈亏及每笔交易 K 线图 |
| 通知内容 | 品种、趋势方向（多头/空头）、建议入场价、止损价、止盈1、止盈2、信号时间 |
| 图表存档 | 实时：`live_suggestions/时间戳/`；回测：`backtest_outputs/ema20_时间戳/trade_charts/` |

---

## 二、使用前准备

### 2.1 环境要求

- Python 3.7+
- 依赖：`requests`、`pandas`、`numpy`、`matplotlib`（与 `EMA20回调策略.py` 相同）

### 2.2 TwelveData API Key

1. 打开 [TwelveData](https://twelvedata.com/apikey) 注册并获取 API Key。
2. 任选一种方式配置：
   - **推荐**：设置环境变量 `TWELVEDATA_API_KEY=你的Key`
   - 或直接修改 `ema20_live_signal.py` 顶部 `TWELVEDATA_API_KEY = "你的Key"`

### 2.3 企业微信机器人（可选）

不配置则只会在控制台打印建议，不发送企业微信。

1. 在企业微信中创建一个群，进入 **群设置 → 群机器人 → 添加机器人**。
2. 复制生成的 **Webhook 地址**（形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx`）。
3. 任选一种方式配置：
   - **推荐**：设置环境变量 `WECHAT_WEBHOOK_URL=你的Webhook地址`
   - 或修改脚本顶部 `WECHAT_WEBHOOK_URL = "你的Webhook地址"`

---

## 三、支持的品种

| 脚本中的名称 | 显示名称 |
|-------------|----------|
| eurusd      | EUR/USD  |
| usdjpy      | USD/JPY  |
| gbpusd      | GBP/USD  |
| xauusd      | XAU/USD  |
| xagusd      | XAG/USD  |

默认监控：`eurusd`、`gbpusd`、`usdjpy`。可通过参数 `--symbols` 修改。

---

## 四、运行方式

在项目目录下执行（PowerShell 或 CMD）：

```bash
python ema20_live_signal.py [选项]
```

### 4.1 常用命令

| 用途 | 命令示例 |
|------|----------|
| 只跑一次检测（默认品种） | `python ema20_live_signal.py --once` |
| 指定品种，跑一次 | `python ema20_live_signal.py --symbols eurusd xauusd --once` |
| 持续监控（每 15 分钟检查一次） | `python ema20_live_signal.py` |
| 自定义检查间隔（如每 5 分钟） | `python ema20_live_signal.py --interval 300` |
| 只把「最近 3 根 K 线」内的信号当当前机会 | `python ema20_live_signal.py --once --recent 3` |
| **回测**：指定时间段、默认品种 | `python ema20_live_signal.py --backtest --start 2026-01-01 --end 2026-03-01` |
| **回测**：指定品种 | `python ema20_live_signal.py --backtest --start 2026-01-01 --end 2026-03-01 --symbols eurusd xauusd` |
| **回测**：不保存每笔交易 K 线图 | `python ema20_live_signal.py --backtest --start 2026-01-01 --end 2026-03-01 --no-charts` |

### 4.2 参数说明

**实时模式：**

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--symbols` | 品种列表，空格分隔 | eurusd gbpusd usdjpy |
| `--once` | 只执行一次检测后退出 | 不加则循环运行 |
| `--interval` | 循环时每隔多少秒执行一次（秒） | 900（15 分钟） |
| `--recent` | 仅将「最近 N 根 K 线」内的信号视为当前机会 | 5 |

**回测模式（与 `--backtest` 同时使用）：**

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--backtest` | 启用回测模式（不再跑实时监控） | — |
| `--start` | 回测开始日期，格式 `YYYY-MM-DD` | 必填 |
| `--end` | 回测结束日期，格式 `YYYY-MM-DD` | 必填 |
| `--symbols` | 要回测的品种列表 | eurusd gbpusd usdjpy |
| `--no-charts` | 回测时不保存每笔交易的 K 线图 | 默认保存 |

### 4.3 示例

```bash
# 只检测欧元、英镑、黄金，跑一次
python ema20_live_signal.py --symbols eurusd gbpusd xauusd --once

# 每 15 分钟检查默认三品种，直到按 Ctrl+C 停止
python ema20_live_signal.py

# 每 30 分钟检查，且只认最近 3 根 K 线内的信号
python ema20_live_signal.py --interval 1800 --recent 3

# 回测：2026-01-01 至 2026-03-01，默认三品种
python ema20_live_signal.py --backtest --start 2026-01-01 --end 2026-03-01

# 回测：仅欧元与黄金，且不保存 K 线图
python ema20_live_signal.py --backtest --start 2026-01-01 --end 2026-03-01 --symbols eurusd xauusd --no-charts
```

---

## 五、输出说明

### 5.1 控制台

- 每个品种会打印：是否发现近期机会、若有机会则打印品种、方向、入场/止损/止盈。
- 若已配置企业微信：会提示「企业微信通知已发送」。

### 5.2 企业微信消息格式（Markdown）

- **品种**、**趋势方向**（多头/空头）
- **建议入场价**、**止损价**、**止盈1**、**止盈2**
- **信号时间**（对应 K 线时间）
- **K 线图已保存**：本地路径说明

### 5.3 实时模式：K 线图保存位置

- 根目录：`live_suggestions/`
- 每次运行一个子目录：`live_suggestions/YYYYMMDD_HHMMSS/`
- 每有一条建议，保存一张图，命名示例：  
  `EUR_USD_多头_20260304_1430.png`（品种_方向_信号时间）

图中包含：K 线、EMA20/50/100、建议入场、止损、止盈1、止盈2 的标注。

### 5.4 回测模式：输出说明

- 控制台：每个品种会打印信号数量、交易次数、胜率、总盈亏、期末资金；最后有各品种汇总。
- 输出根目录：`backtest_outputs/ema20_YYYYMMDD_HHMMSS/`
- 每笔交易 K 线图（未加 `--no-charts` 时）：`backtest_outputs/ema20_时间戳/trade_charts/`，命名示例：`0001_EUR_USD_20260112_0830.png`

---

## 六、在代码中调用

**实时检测一次：**

```python
from ema20_live_signal import run_live_check, WECHAT_WEBHOOK_URL

# 指定品种、企业微信（可选）、仅最近 5 根 K 线内的信号
suggestions = run_live_check(
    symbols=["eurusd", "xauusd"],
    wechat_webhook=WECHAT_WEBHOOK_URL,
    recent_bars_threshold=5,
)
# suggestions 为列表，每项含 symbol, trend, entry, stop, tp1, tp2, signal_time, chart_path
```

**回测指定时间段：**

```python
from ema20_live_signal import run_backtest_mode

# 回测 eurusd、xauusd 在 2026-01-01 ～ 2026-03-01 的表现
results = run_backtest_mode(
    symbols=["eurusd", "xauusd"],
    start_date="2026-01-01",
    end_date="2026-03-01",
    save_charts=True,
)
# results 为字典：{ "eurusd": { "trades", "summary", "signals_count" }, ... }
```

---

## 七、常见问题

**Q：提示「请设置有效的 TWELVEDATA_API_KEY」**  
A：按「二、使用前准备」配置 API Key（环境变量或脚本内默认值）。

**Q：没有收到企业微信消息**  
A：检查 `WECHAT_WEBHOOK_URL` 是否配置正确；控制台是否出现「企业微信通知已发送」。若返回 errcode，可根据企业微信文档排查 Webhook 与消息格式。

**Q：某个品种报错或跳过**  
A：可能是该品种暂无足够数据或 API 限流。脚本会继续处理其他品种；可稍后重试或减少品种数量。

**Q：想改成 5 分钟或其它周期**  
A：当前脚本固定为 15 分钟。若需其它周期，需在脚本中把 `load_latest_15min_bars` 的 `interval` 及指标计算逻辑与策略周期对齐（策略逻辑仍在 `EMA20回调策略.py`）。

**Q：如何长时间后台运行？**  
A：可用系统计划任务/ cron 定时执行 `--once`；或在服务器上用 `nohup`/`screen` 运行不带 `--once` 的循环，并配合 `--interval`。

**Q：回测模式提示需要 --start 和 --end**  
A：使用 `--backtest` 时必须同时指定 `--start YYYY-MM-DD` 和 `--end YYYY-MM-DD`，例如：`--backtest --start 2026-01-01 --end 2026-03-01`。

**Q：回测用的数据和实时一样吗？**  
A：是。回测使用同一套 15 分钟 K 线与 EMA20 回调策略逻辑（TwelveData API + `EMA20回调策略.py`），仅时间范围由你指定。

---

## 八、文件与依赖关系

- **ema20_live_signal.py**：本程序主脚本，会调用 `EMA20回调策略.py` 中的策略类；支持实时检测与回测两种模式。
- **EMA20回调策略.py**：提供 EMA20 回调策略的信号与止损止盈逻辑，无需单独运行。
- **live_suggestions/**：由本程序自动创建，用于存放实时模式下每次发出建议时的 K 线图。
- **backtest_outputs/**：回测模式下自动创建，其下 `ema20_时间戳/trade_charts/` 存放每笔回测交易的 K 线图。

---

*使用手册对应脚本：`ema20_live_signal.py`*
