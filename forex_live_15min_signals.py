"""
15分钟外汇策略实时监控 — 企业微信通知 + K线图归档 / 回测模式

基于 forex_backtest_15min_model 的多周期趋势+突破回调策略，
拉取指定品种最新 15 分钟 K 线，检测到交易机会后通过企业微信机器人推送
（品种、趋势方向、建议买入价、止盈止损），并在本地目录保存发出建议时的 K 线图。

数据源: Twelve Data (15min)

运行示例:
  实时监控:     python forex_live_15min_signals.py [--once]
  回测模式:     python forex_live_15min_signals.py --backtest --start-date 2024-01-01 --end-date 2024-06-01
  回测单品种:   python forex_live_15min_signals.py --backtest --symbol EURUSD --start-date 2024-01-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# 修复 Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from forex_backtest_15min_model import Forex15MinBacktester, StrategyConfig


# ============================================================
# 配置区
# ============================================================
# 企业微信机器人 Webhook（群聊 → 添加群机器人 → 复制地址）
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128")

# Twelve Data API Key（twelvedata.com 免费注册）
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "61141e293ece4cad906e65413921b012")

# 要监控的外汇品种（与回测模型支持的品种一致）
SYMBOLS = ["EURUSD", "USDJPY", "AUDUSD", "XAUUSD", "GBPUSD"]

# 扫描间隔（秒），建议 300 = 5 分钟，与 K 线周期对齐
SCAN_INTERVAL_SECONDS = 300

# 每次拉取 15m K 线根数（策略 find_setups 至少需要 300）
LIVE_BARS = 300

# 限流重试：遇到 429/rate limit 时的等待（秒），逐次增加，最大不超过此值
RATE_LIMIT_WAIT_BASE = 30
RATE_LIMIT_WAIT_MAX = 120
RATE_LIMIT_MAX_ATTEMPTS = 6

# 仅推送“新鲜”信号：entry_i >= len(df) - N 时才推送
SIGNAL_FRESH_BARS = 5

# K 线图根目录；每次发出建议时在其下按时间建子目录
CHART_OUTPUT_DIR = "live_15m_signals"

# 图表中显示的 K 线根数（最近 N 根）
CHART_BARS = 1000

# 已发送信号缓存文件（防止同一机会重复推送）
SIGNAL_CACHE_FILE = "live_15m_sent_signals.json"

# Twelve Data 品种映射（与 forex_backtest_15min_model 一致）
SYMBOL_TO_TWELVEDATA = {
    "EURUSD": "EUR/USD",
    "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD",
    "XAUUSD": "XAU/USD",
    "GBPUSD": "GBP/USD",
}
# ============================================================


def fetch_latest_15m(symbol: str, api_key: str, bars: int = LIVE_BARS) -> pd.DataFrame | None:
    """
    从 Twelve Data 拉取指定品种最新 bars 根 15 分钟 K 线。
    返回列 Open/High/Low/Close、datetime 索引、按时间升序的 DataFrame。
    """
    td_symbol = SYMBOL_TO_TWELVEDATA.get(symbol.upper())
    if not td_symbol:
        print(f"[{symbol}] 不支持的品种，跳过")
        return None
    if not api_key or api_key == "YOUR_TWELVEDATA_API_KEY":
        print("未配置 TWELVEDATA_API_KEY")
        return None

    base_url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": td_symbol,
        "interval": "15min",
        "outputsize": min(bars, 5000),
        "apikey": api_key,
        "format": "JSON",
        "timezone": "UTC",
        "order": "DESC",
    }

    data = None
    last_error = None
    attempt = 0
    max_attempts = 4

    while attempt < max_attempts:
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            raw = resp.json() if resp.content else {}
            # 先判断是否限流（HTTP 429 或 API 返回的限流信息）
            is_rate_limit = (
                resp.status_code == 429
                or "rate limit" in str(raw.get("message", "")).lower()
                or "too many requests" in str(raw.get("message", "")).lower()
                or (isinstance(raw.get("code"), int) and raw.get("code") == 429)
            )
            if is_rate_limit:
                max_attempts = max(max_attempts, RATE_LIMIT_MAX_ATTEMPTS)
                wait_sec = min(RATE_LIMIT_WAIT_BASE * (attempt + 1), RATE_LIMIT_WAIT_MAX)
                print(f"[{symbol}] 触发限流，等待 {wait_sec} 秒后重试 ({attempt + 1}/{max_attempts})...")
                time.sleep(wait_sec)
                attempt += 1
                continue
            resp.raise_for_status()
            data = raw
            break
        except requests.exceptions.HTTPError as e:
            last_error = e
            if e.response is not None and e.response.status_code == 429:
                max_attempts = max(max_attempts, RATE_LIMIT_MAX_ATTEMPTS)
                wait_sec = min(RATE_LIMIT_WAIT_BASE * (attempt + 1), RATE_LIMIT_WAIT_MAX)
                print(f"[{symbol}] 触发限流(429)，等待 {wait_sec} 秒后重试 ({attempt + 1}/{max_attempts})...")
                time.sleep(wait_sec)
            else:
                time.sleep(min(5 * (attempt + 1), 20))
            attempt += 1
            continue
        except Exception as e:
            last_error = e
            if attempt == max_attempts - 1:
                print(f"[{symbol}] 拉取数据失败: {e}")
                return None
            time.sleep(min(5 * (attempt + 1), 20))
            attempt += 1
            continue

    if data is None:
        if last_error:
            print(f"[{symbol}] 拉取数据失败: {last_error}")
        return None

    err = data.get("code") or data.get("status")
    if err and "error" in str(err).lower():
        print(f"[{symbol}] API 错误: {data.get('message', data)}")
        return None

    values = data.get("values", [])
    if not values:
        print(f"[{symbol}] 无数据")
        return None

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
        }
    )
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df = df[~df.index.duplicated(keep="first")].sort_index()

    if len(df) < 300:
        print(f"[{symbol}] 数据不足 300 根，当前 {len(df)} 根")
        return None
    return df


def fetch_15m_range(
    symbol: str,
    start_date: str,
    end_date: str,
    api_key: str,
) -> pd.DataFrame | None:
    """
    从 Twelve Data 拉取指定品种在 [start_date, end_date] 内的 15 分钟 K 线（回测用）。
    支持跨多个月时自动分页（每批最多 5000 根）。
    返回列 Open/High/Low/Close、datetime 索引、按时间升序的 DataFrame。
    """
    td_symbol = SYMBOL_TO_TWELVEDATA.get(symbol.upper())
    if not td_symbol:
        print(f"[{symbol}] 不支持的品种，跳过")
        return None
    if not api_key or api_key == "YOUR_TWELVEDATA_API_KEY":
        print("未配置 TWELVEDATA_API_KEY")
        return None

    base_url = "https://api.twelvedata.com/time_series"
    all_frames: list[pd.DataFrame] = []
    req_end = end_date
    attempt = 0
    max_attempts = 4

    while True:
        params = {
            "symbol": td_symbol,
            "interval": "15min",
            "outputsize": 5000,
            "apikey": api_key,
            "format": "JSON",
            "timezone": "UTC",
            "order": "DESC",
            "start_date": start_date,
            "end_date": req_end,
        }

        data = None
        for _ in range(max_attempts):
            try:
                resp = requests.get(base_url, params=params, timeout=30)
                raw = resp.json() if resp.content else {}
                is_rate_limit = (
                    resp.status_code == 429
                    or "rate limit" in str(raw.get("message", "")).lower()
                    or "too many requests" in str(raw.get("message", "")).lower()
                )
                if is_rate_limit:
                    wait_sec = min(RATE_LIMIT_WAIT_BASE * (attempt + 1), RATE_LIMIT_WAIT_MAX)
                    print(f"[{symbol}] 触发限流，等待 {wait_sec} 秒后重试...")
                    time.sleep(wait_sec)
                    attempt += 1
                    continue
                resp.raise_for_status()
                data = raw
                break
            except Exception as e:
                time.sleep(min(5 * (attempt + 1), 20))
                attempt += 1
                continue

        if data is None:
            print(f"[{symbol}] 拉取区间数据失败")
            break

        err = data.get("code") or data.get("status")
        if err and "error" in str(err).lower():
            print(f"[{symbol}] API 错误: {data.get('message', data)}")
            break

        values = data.get("values", [])
        if not values:
            break

        df_batch = pd.DataFrame(values)
        df_batch["datetime"] = pd.to_datetime(df_batch["datetime"])
        df_batch = df_batch.set_index("datetime").rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
            }
        )
        for col in ["Open", "High", "Low", "Close"]:
            df_batch[col] = pd.to_numeric(df_batch[col], errors="coerce")
        df_batch = df_batch[["Open", "High", "Low", "Close"]].dropna()
        all_frames.append(df_batch)

        if len(values) < 5000:
            break
        oldest_ts = df_batch.index.min()
        req_end = (oldest_ts - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(1)

    if not all_frames:
        print(f"[{symbol}] 区间内无数据")
        return None

    combined = pd.concat(all_frames)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    if combined.index.tz is not None:
        start_ts = start_ts.tz_localize(combined.index.tz) if start_ts.tzinfo is None else start_ts
        end_ts = end_ts.tz_localize(combined.index.tz) if end_ts.tzinfo is None else end_ts
    combined = combined[(combined.index >= start_ts) & (combined.index <= end_ts)]

    if len(combined) < 300:
        print(f"[{symbol}] 数据不足 300 根，当前 {len(combined)} 根")
        return None
    print(f"[{symbol}] 回测区间加载完成: {len(combined)} 根 15m K线 ({combined.index[0]} ~ {combined.index[-1]})")
    return combined


def send_wecom_markdown(content: str) -> bool:
    """发送企业微信 Markdown 消息"""
    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print(f"  [企业微信模拟] {content[:100]}...")
        return True
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode") != 0:
            print(f"  [企业微信发送失败] {result.get('errmsg', 'Unknown error')}")
            return False
        return True
    except Exception as e:
        print(f"  [企业微信发送失败] {e}")
        return False


def send_wecom_text(message: str) -> bool:
    """发送企业微信文本消息"""
    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print(f"  [企业微信模拟文本] {message[:80]}...")
        return True
    payload = {"msgtype": "text", "text": {"content": message}}
    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode") != 0:
            return False
        return True
    except Exception as e:
        print(f"  [企业微信发送失败] {e}")
        return False


def load_sent_signals() -> set:
    if os.path.exists(SIGNAL_CACHE_FILE):
        try:
            with open(SIGNAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_sent_signals(sent: set) -> None:
    # 只保留最近 7 天，key 格式: symbol|direction|YYYY-MM-DD HH:MM
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    filtered = set()
    for s in sent:
        parts = s.split("|")
        if len(parts) >= 3 and parts[2][:10] >= cutoff:
            filtered.add(s)
    with open(SIGNAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(filtered), f, indent=2, ensure_ascii=False)


def _price_decimals(symbol: str, price: float) -> int:
    if "JPY" in symbol.upper() or symbol.upper() in ("XAUUSD", "XAGUSD"):
        return 2
    return 5


def format_signal_markdown(symbol: str, trade_info: dict, now_str: str) -> str:
    """组装企业微信 Markdown 通知内容"""
    d = trade_info["direction"]
    direction_cn = "做多" if d == "long" else "做空"
    dec = _price_decimals(symbol, trade_info["entry"])
    entry = trade_info["entry"]
    stop = trade_info["stop"]
    tp1 = trade_info["tp1"]
    tp2 = trade_info["tp2"]
    entry_time = trade_info.get("entry_time", "")

    lines = [
        f"## 15分钟策略 — 交易建议",
        "",
        f"**品种**：{symbol}",
        f"**趋势方向**：{direction_cn}",
        f"**建议买入价格**：{entry:.{dec}f}",
        f"**止损价格**：{stop:.{dec}f}",
        f"**止盈 TP1**：{tp1:.{dec}f}",
        f"**止盈 TP2**：{tp2:.{dec}f}",
        f"**信号时间**：{entry_time}",
        f"**推送时间**：{now_str}",
        "",
        "基于最近 300 根 15 分钟 K 线，多周期趋势+突破回调策略。",
    ]
    return "\n".join(lines)


def save_live_signal_chart(
    symbol: str,
    df_15m: pd.DataFrame,
    trade_info: dict,
    chart_dir: str,
    show_bars: int = CHART_BARS,
) -> str | None:
    """
    保存发出建议时的 K 线图：最近 show_bars 根 15m、EMA20/50/100、
    以及 entry/stop/tp1/tp2 水平线与入场标记。
    返回保存的文件路径，失败返回 None。
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # 显示最近 show_bars 根 K 线
    start_i = max(0, len(df_15m) - show_bars)
    end_i = len(df_15m)
    d = df_15m.iloc[start_i:end_i].copy()

    if d.empty:
        return None

    entry_price = float(trade_info["entry"])
    stop_price = float(trade_info["stop"])
    tp1_price = float(trade_info["tp1"])
    tp2_price = float(trade_info["tp2"])
    direction = trade_info.get("direction", "long")

    fig, ax = plt.subplots(figsize=(14, 6))
    x = mdates.date2num(d.index.to_pydatetime())
    bar_width = 0.006

    for idx in range(len(d)):
        row = d.iloc[idx]
        op, hi, lo, cl = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        color = "#26a69a" if cl >= op else "#ef5350"
        ax.plot([x[idx], x[idx]], [lo, hi], color=color, linewidth=0.8)
        body_bottom = min(op, cl)
        body_h = max(abs(cl - op), 1e-8)
        rect = mpatches.Rectangle(
            (x[idx] - bar_width / 2, body_bottom),
            bar_width,
            body_h,
            facecolor=color,
            edgecolor=color,
            alpha=0.85,
        )
        ax.add_patch(rect)

    if "ema_20" in d.columns:
        ax.plot(d.index, d["ema_20"], color="#1f77b4", linewidth=1.2, label="EMA20")
    if "ema_50" in d.columns:
        ax.plot(d.index, d["ema_50"], color="#ff7f0e", linewidth=1.2, label="EMA50")
    if "ema_100" in d.columns:
        ax.plot(d.index, d["ema_100"], color="#9467bd", linewidth=1.2, label="EMA100")

    ax.axhline(entry_price, color="blue", linestyle="--", linewidth=1, alpha=0.8, label="建议入场")
    ax.axhline(stop_price, color="red", linestyle="--", linewidth=1, alpha=0.8, label="止损")
    ax.axhline(tp1_price, color="green", linestyle=":", linewidth=0.9, alpha=0.7, label="TP1")
    ax.axhline(tp2_price, color="darkgreen", linestyle=":", linewidth=0.9, alpha=0.7, label="TP2")

    entry_ts = trade_info.get("entry_time")
    if entry_ts:
        try:
            t = pd.to_datetime(entry_ts)
            if t.tzinfo is not None:
                t = t.tz_localize(None)
            ax.scatter(
                t, entry_price, s=120, marker="^" if direction == "long" else "v",
                color="blue", edgecolors="white", zorder=5,
            )
        except Exception:
            pass

    ax.set_title(f"{symbol} 15m 建议 {direction.upper()} — 入场 {entry_price} 止损 {stop_price} TP1 {tp1_price} TP2 {tp2_price}")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()

    Path(chart_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{symbol}_{direction}_{ts}.png"
    file_path = Path(chart_dir) / filename
    fig.savefig(file_path, dpi=140)
    plt.close(fig)
    return str(file_path)


def save_symbol_snapshot_chart(
    symbol: str,
    df_15m: pd.DataFrame,
    chart_dir: str,
    show_bars: int = CHART_BARS,
    snapshot_time: str | None = None,
) -> str | None:
    """
    每次触发监控时保存该品种当时的 K 线快照（无信号也保存）。
    含最近 show_bars 根 15m、EMA20/50/100，无入场/止损止盈线。
    返回保存的文件路径，失败返回 None。
    """
    if snapshot_time is None:
        snapshot_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    start_i = max(0, len(df_15m) - show_bars)
    end_i = len(df_15m)
    d = df_15m.iloc[start_i:end_i].copy()
    if d.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))
    x = mdates.date2num(d.index.to_pydatetime())
    bar_width = 0.006

    for idx in range(len(d)):
        row = d.iloc[idx]
        op, hi, lo, cl = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        color = "#26a69a" if cl >= op else "#ef5350"
        ax.plot([x[idx], x[idx]], [lo, hi], color=color, linewidth=0.8)
        body_bottom = min(op, cl)
        body_h = max(abs(cl - op), 1e-8)
        rect = mpatches.Rectangle(
            (x[idx] - bar_width / 2, body_bottom),
            bar_width,
            body_h,
            facecolor=color,
            edgecolor=color,
            alpha=0.85,
        )
        ax.add_patch(rect)

    if "ema_20" in d.columns:
        ax.plot(d.index, d["ema_20"], color="#1f77b4", linewidth=1.2, label="EMA20")
    if "ema_50" in d.columns:
        ax.plot(d.index, d["ema_50"], color="#ff7f0e", linewidth=1.2, label="EMA50")
    if "ema_100" in d.columns:
        ax.plot(d.index, d["ema_100"], color="#9467bd", linewidth=1.2, label="EMA100")

    ax.set_title(f"{symbol} 15m 监控快照 — {snapshot_time}")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()

    Path(chart_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{symbol}_snapshot_{ts}.png"
    file_path = Path(chart_dir) / filename
    fig.savefig(file_path, dpi=140)
    plt.close(fig)
    return str(file_path)


def run_backtest_mode(
    symbols: list[str],
    start_date: str,
    end_date: str,
    api_key: str,
    save_charts: bool = True,
    chart_output_base: str = "backtest_outputs",
) -> list[dict]:
    """
    回测模式：对指定品种在 [start_date, end_date] 内运行 15 分钟策略回测。
    返回各品种的回测结果列表。
    """
    from pathlib import Path

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(chart_output_base) / f"15min_live_{ts}"
    if save_charts:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "trade_charts").mkdir(exist_ok=True)

    print("=" * 60)
    print("  15分钟策略 — 回测模式")
    print(f"  区间: {start_date} ~ {end_date}")
    print(f"  品种: {', '.join(symbols)}")
    print(f"  数据源: Twelve Data 15min")
    if save_charts:
        print(f"  图表目录: {run_dir.resolve()}")
    print("=" * 60)

    all_results = []
    for symbol in symbols:
        print(f"\n[{symbol}] 加载数据...")
        df = fetch_15m_range(symbol, start_date, end_date, api_key)
        if df is None or len(df) < 300:
            print(f"[{symbol}] 数据不足，跳过")
            continue

        bt = Forex15MinBacktester(
            symbol,
            start_date=start_date,
            end_date=end_date,
            config=StrategyConfig(risk_per_trade=0.02),
        )
        bt.df_15m = df.copy()
        bt._calc_indicators()

        setups = bt.find_setups()
        print(f"[{symbol}] 信号数: {len(setups)}")
        if not setups:
            continue

        result = bt.run_backtest(setups)
        if result is None:
            print(f"[{symbol}] 无有效交易")
            continue

        ret_pct = (result["final_capital"] - 100000.0) / 100000.0 * 100
        print(
            f"[{symbol}] 交易={result['total_trades']} 胜率={result['win_rate']:.1f}% "
            f"PF={result['profit_factor']:.2f} 最大回撤={result['max_drawdown']:.1f}% "
            f"夏普={result['sharpe']:.2f} 收益率={ret_pct:.1f}%"
        )

        chart_files = []
        if save_charts and result.get("trades"):
            symbol_chart_dir = run_dir / "trade_charts" / symbol
            symbol_chart_dir.mkdir(parents=True, exist_ok=True)
            for idx, tr in enumerate(result["trades"], 1):
                fp = bt.plot_single_trade_chart(tr, idx, str(symbol_chart_dir))
                if fp:
                    chart_files.append(fp)
            print(f"[{symbol}] 已保存交易图: {len(chart_files)} 张")

        result["chart_files"] = chart_files
        all_results.append({"symbol": symbol, "result": result})
        time.sleep(1)

    if all_results:
        print("\n" + "-" * 60)
        print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'PF':>8} {'最大回撤':>10} {'收益率':>10}")
        print("-" * 60)
        for r in all_results:
            m = r["result"]
            ret_pct = (m["final_capital"] - 100000.0) / 100000.0 * 100
            print(
                f"{r['symbol']:<10} {m['total_trades']:>8} {m['win_rate']:>7.1f}% "
                f"{m['profit_factor']:>8.2f} {m['max_drawdown']:>9.1f}% {ret_pct:>9.1f}%"
            )
        if save_charts:
            print(f"\n图表目录: {(run_dir / 'trade_charts').resolve()}")

    return all_results


def scan_once(sent_signals: set) -> tuple[set, int]:
    """
    执行一次扫描：对每个品种拉数据、找信号、过滤新鲜、算价格、通知+存图+写缓存。
    返回 (更新后的 sent_signals, 本次新推送数量)。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_count = 0
    # 本次发出建议时使用的 K 线图子目录（按次归档）
    chart_subdir = datetime.now().strftime("%Y%m%d_%H%M%S")
    chart_dir = os.path.join(CHART_OUTPUT_DIR, chart_subdir)

    for symbol in SYMBOLS:
        df = fetch_latest_15m(symbol, TWELVEDATA_API_KEY, bars=LIVE_BARS)
        if df is None or len(df) < 300:
            continue

        bt = Forex15MinBacktester(
            symbol,
            start_date="2020-01-01",
            end_date="2025-12-31",
            config=StrategyConfig(risk_per_trade=0.02),
        )
        bt.df_15m = df.copy()
        bt._calc_indicators()

        # 每次触发监控都记录该品种当时的 K 线图
        snap_path = save_symbol_snapshot_chart(
            symbol, bt.df_15m, chart_dir, show_bars=CHART_BARS, snapshot_time=now_str
        )
        if snap_path:
            print(f"  [{symbol}] K线快照已保存: {snap_path}")

        setups = bt.find_setups()
        n = len(df)
        fresh_setups = [s for s in setups if s["entry_i"] >= n - SIGNAL_FRESH_BARS]

        for s in fresh_setups:
            res = bt.run_backtest([s], max_closed_trades=1)
            if not res or not res.get("trades"):
                continue
            trade = res["trades"][0]
            entry_time = trade.get("entry_time", "")
            # 唯一键：品种|方向|信号时间(15分钟粒度)
            try:
                t = pd.to_datetime(entry_time)
                if t.tzinfo:
                    t = t.tz_localize(None)
                key_time = t.strftime("%Y-%m-%d %H:%M")
            except Exception:
                key_time = entry_time[:16] if len(entry_time) >= 16 else entry_time
            sig_id = f"{symbol}|{trade['direction']}|{key_time}"
            if sig_id in sent_signals:
                continue

            trade_info = {
                "entry": trade["entry"],
                "stop": trade["stop"],
                "tp1": trade["tp1"],
                "tp2": trade["tp2"],
                "direction": trade["direction"],
                "entry_i": trade["entry_i"],
                "entry_time": entry_time,
            }

            chart_path = save_live_signal_chart(symbol, bt.df_15m, trade_info, chart_dir, show_bars=CHART_BARS)
            if chart_path:
                print(f"  K线图已保存: {chart_path}")

            msg = format_signal_markdown(symbol, trade_info, now_str)
            if send_wecom_markdown(msg):
                sent_signals.add(sig_id)
                save_sent_signals(sent_signals)
                new_count += 1
                dec = _price_decimals(symbol, trade["entry"])
                print(f"  已推送: {symbol} {trade['direction'].upper()} 入场={trade['entry']:.{dec}f} 止损={trade['stop']:.{dec}f}")
            else:
                print("  推送失败，下次重试")

        time.sleep(1)

    return sent_signals, new_count


def main() -> None:
    parser = argparse.ArgumentParser(description="15分钟策略实时监控 — 企业微信通知 + K线图 / 回测模式")
    parser.add_argument("--once", action="store_true", help="仅执行一次扫描后退出")
    parser.add_argument("--backtest", action="store_true", help="切换到回测模式，对指定品种与时间段进行回测")
    parser.add_argument("--start-date", type=str, default=None, help="回测开始日期，如 2024-01-01（仅回测模式）")
    parser.add_argument("--end-date", type=str, default=None, help="回测结束日期，如 2024-12-01（仅回测模式）")
    parser.add_argument("--symbol", type=str, action="append", default=None, help="回测品种，可多次指定如 --symbol EURUSD --symbol XAUUSD；不指定则使用配置中全部品种")
    parser.add_argument("--no-charts", action="store_true", help="回测时不保存交易图表")
    args = parser.parse_args()

    # ---------- 回测模式 ----------
    if args.backtest:
        end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
        start_date = args.start_date or (datetime.now() - timedelta(days=220)).strftime("%Y-%m-%d")
        symbols = args.symbol if args.symbol else SYMBOLS
        symbols = [s.upper() for s in symbols]
        if not TWELVEDATA_API_KEY or TWELVEDATA_API_KEY == "YOUR_TWELVEDATA_API_KEY":
            print("请在环境变量或脚本顶部配置 TWELVEDATA_API_KEY")
            return
        run_backtest_mode(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            api_key=TWELVEDATA_API_KEY,
            save_charts=not args.no_charts,
        )
        return

    # ---------- 实时监控模式 ----------
    print("=" * 60)
    print("  15分钟策略实时监控 (企业微信 + K线图)")
    print(f"  数据源: Twelve Data 15min")
    print(f"  监控品种: {', '.join(SYMBOLS)}")
    print(f"  拉取根数: {LIVE_BARS} | 新鲜信号: 最近 {SIGNAL_FRESH_BARS} 根")
    print(f"  图表目录: {CHART_OUTPUT_DIR}/")
    if args.once:
        print("  模式: 单次扫描 (--once)")
    else:
        print(f"  扫描间隔: {SCAN_INTERVAL_SECONDS} 秒")
    print("=" * 60)

    if not TWELVEDATA_API_KEY or TWELVEDATA_API_KEY == "YOUR_TWELVEDATA_API_KEY":
        print("\n请在环境变量或脚本顶部配置 TWELVEDATA_API_KEY")
        return

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("\n缺少 matplotlib，K线图将不保存。可执行: pip install matplotlib")

    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("\n未配置 WECOM_WEBHOOK_URL，将以模拟模式运行（不真实推送）")
    else:
        send_wecom_text(
            f"15分钟策略监控已启动\n"
            f"品种: {', '.join(SYMBOLS)}\n"
            f"扫描间隔: {SCAN_INTERVAL_SECONDS}秒\n"
            f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)
    sent_signals = load_sent_signals()

    while True:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[扫描] {now_str}")
        try:
            sent_signals, new_count = scan_once(sent_signals)
            if new_count == 0:
                print("  本次无新信号")
            else:
                print(f"  本次推送 {new_count} 个新信号")
        except KeyboardInterrupt:
            print("\n已手动停止 (Ctrl+C)")
            if WECOM_WEBHOOK_URL != "YOUR_WEBHOOK_URL":
                send_wecom_text("15分钟策略监控已停止")
            break
        except Exception as e:
            print(f"  扫描异常: {e}")
            if WECOM_WEBHOOK_URL != "YOUR_WEBHOOK_URL":
                send_wecom_text(f"15分钟策略监控异常: {e}")

        if args.once:
            break

        next_scan = datetime.now() + timedelta(seconds=SCAN_INTERVAL_SECONDS)
        print(f"\n下次扫描: {next_scan.strftime('%Y-%m-%d %H:%M:%S')} (按 Ctrl+C 停止)")
        for remaining in range(SCAN_INTERVAL_SECONDS, 0, -60):
            time.sleep(min(60, remaining))
            if remaining > 60:
                print(f"  距下次扫描还有 {remaining // 60} 分钟...", end="\r")


if __name__ == "__main__":
    main()
