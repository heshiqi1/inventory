"""
EMA20 回调策略 - 实时交易机会检测
基于最新 200 根 15 分钟 K 线检测交易机会，通过企业微信机器人通知，并保存建议时的 K 线图。
"""

import os
import sys
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import warnings
warnings.filterwarnings("ignore")

# 导入策略回测器（复用信号与止损止盈逻辑）
from importlib.util import spec_from_file_location, module_from_spec
_script_dir = os.path.dirname(os.path.abspath(__file__))
_spec = spec_from_file_location("ema20_strategy", os.path.join(_script_dir, "EMA20回调策略.py"))
_ema20 = module_from_spec(_spec)
_spec.loader.exec_module(_ema20)
CompleteStrategyBacktester = _ema20.CompleteStrategyBacktester

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "61141e293ece4cad906e65413921b012")
# 企业微信机器人 Webhook（创建群机器人后复制 URL，或设置环境变量 WECHAT_WEBHOOK_URL）
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128")

# 支持的品种与 TwelveData 符号
SYMBOL_MAP = {
    "eurusd": "EUR/USD",
    "usdjpy": "USD/JPY",
    "gbpusd": "GBP/USD",
    "xauusd": "XAU/USD",
    "xagusd": "XAG/USD",
}

# 默认监控品种（可修改）
DEFAULT_SYMBOLS = ["eurusd", "gbpusd", "usdjpy"]

# 实时建议输出根目录
LIVE_SUGGESTIONS_ROOT = "live_suggestions"

# 回测模式输出根目录
BACKTEST_OUTPUTS_ROOT = "backtest_outputs"

# 快照/建议图中显示的 K 线根数（最近 N 根）
CHART_BARS = 200

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


def fetch_twelvedata_with_retry(url: str, params: dict, max_retry: int = 5) -> dict:
    """请求 TwelveData，限流时等待重试"""
    for attempt in range(max_retry):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if attempt == max_retry - 1:
                raise RuntimeError(f"TwelveData 请求失败: {e}") from e
            time.sleep(min(5 * (attempt + 1), 20))
            continue
        if data.get("status") != "error":
            return data
        msg = str(data.get("message", "")).lower()
        if "run out of api credits" in msg or "wait for the next minute" in msg:
            wait_s = (60 - datetime.now().second) + 2
            print(f"  触发分钟限流，等待 {wait_s} 秒...")
            time.sleep(wait_s)
            continue
        raise ValueError(f"TwelveData API 错误: {data.get('message', '未知')}")
    raise RuntimeError("TwelveData 请求重试后仍失败")


def load_latest_15min_bars(symbol: str, api_key: str, n: int = 200) -> pd.DataFrame:
    """获取指定品种最新 n 根 15 分钟 K 线，并计算与策略一致的指标"""
    td_symbol = SYMBOL_MAP.get(symbol.lower())
    if not td_symbol:
        raise ValueError(f"不支持的品种: {symbol}，支持: {list(SYMBOL_MAP.keys())}")

    base_url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": td_symbol,
        "interval": "15min",
        "outputsize": n,
        "apikey": api_key,
        "format": "JSON",
        "timezone": "UTC",
        "order": "DESC",
    }
    data = fetch_twelvedata_with_retry(base_url, params)
    values = data.get("values", [])
    if not values:
        raise ValueError(f"未获取到 {td_symbol} 数据")

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
    })
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_index()

    # 与 EMA20 策略一致的指标
    df["ema_20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["ema_100"] = df["Close"].ewm(span=100, adjust=False).mean()
    df["range"] = df["High"] - df["Low"]
    df["body"] = np.abs(df["Close"] - df["Open"])
    df["body_ratio"] = df["body"] / df["range"].replace(0, np.nan)
    df["upper_shadow"] = np.where(
        df["Close"] > df["Open"], df["High"] - df["Close"], df["High"] - df["Open"]
    )
    df["lower_shadow"] = np.where(
        df["Close"] > df["Open"], df["Open"] - df["Low"], df["Close"] - df["Low"]
    )
    df["is_trend_bar"] = df["body"] > (df["upper_shadow"] + df["lower_shadow"]) * 2
    df["direction"] = np.where(df["Close"] > df["Open"], 1, -1)
    df["atr"] = df["range"].rolling(14).mean()
    df["high_20"] = df["High"].rolling(20).max()
    df["low_20"] = df["Low"].rolling(20).min()
    return df


def plot_suggestion_chart(bt, signal: dict, symbol_display: str, save_dir: str) -> str:
    """绘制建议时的 K 线图（含 EMA、入场、止损、止盈标注）"""
    idx = signal["idx"]
    entry = signal["entry"]
    stop = signal["stop"]
    trend = signal["trend"]
    tp1, tp2 = bt.find_take_profit(idx, entry, stop, trend)

    left = max(0, idx - 80)
    right = min(len(bt.df), idx + 40)
    plot_df = bt.df.iloc[left:right].copy()
    entry_time = bt.df.index[idx]

    fig, ax = plt.subplots(figsize=(14, 6))
    x = mdates.date2num(plot_df.index.to_pydatetime())
    candle_width = (15 / (24 * 60)) * 0.7

    for i, (_, row) in enumerate(plot_df.iterrows()):
        color = "#2ca02c" if row["Close"] >= row["Open"] else "#d62728"
        ax.vlines(x[i], row["Low"], row["High"], color=color, linewidth=1.0, alpha=0.9)
        body_low = min(row["Open"], row["Close"])
        body_h = max(abs(row["Close"] - row["Open"]), 1e-8)
        rect = Rectangle(
            (x[i] - candle_width / 2, body_low), candle_width, body_h,
            facecolor=color, edgecolor=color, linewidth=0.8, alpha=0.9
        )
        ax.add_patch(rect)

    ax.plot(x, plot_df["ema_20"].values, color="orange", linewidth=1.5, label="EMA20")
    ax.plot(x, plot_df["ema_50"].values, color="blue", linewidth=1.2, label="EMA50")
    ax.plot(x, plot_df["ema_100"].values, color="purple", linewidth=1.0, label="EMA100")

    entry_x = mdates.date2num(entry_time)
    ax.scatter(entry_x, entry, marker="^", s=140, color="blue", zorder=5, label="建议入场")
    ax.axhline(entry, color="blue", linestyle="--", linewidth=1.0, alpha=0.6)
    ax.axhline(stop, color="red", linestyle="--", linewidth=1.0, alpha=0.8, label="止损")
    ax.axhline(tp1, color="green", linestyle=":", linewidth=1.0, alpha=0.8, label="止盈1")
    ax.axhline(tp2, color="darkgreen", linestyle=":", linewidth=1.0, alpha=0.8, label="止盈2")

    direction_cn = "多头" if trend == "uptrend" else "空头"
    ax.set_title(f"{symbol_display} | {direction_cn} | 建议时间 {entry_time}")
    ax.set_ylabel("价格")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()

    safe_name = symbol_display.replace("/", "_")
    fname = f"{safe_name}_{direction_cn}_{entry_time.strftime('%Y%m%d_%H%M')}.png"
    path = os.path.join(save_dir, fname)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_snapshot_chart(bt, symbol_display: str, save_dir: str, show_bars: int = None) -> str | None:
    """
    每次触发监控时保存该品种当时的 K 线快照（仅 K 线 + EMA，无入场/止损止盈）。
    有信号时也会为每笔建议单独保存带标注的图（见 plot_suggestion_chart）。
    """
    show_bars = show_bars if show_bars is not None else CHART_BARS
    left = max(0, len(bt.df) - show_bars)
    plot_df = bt.df.iloc[left:].copy()
    if plot_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))
    x = mdates.date2num(plot_df.index.to_pydatetime())
    candle_width = (15 / (24 * 60)) * 0.7

    for i, (_, row) in enumerate(plot_df.iterrows()):
        color = "#2ca02c" if row["Close"] >= row["Open"] else "#d62728"
        ax.vlines(x[i], row["Low"], row["High"], color=color, linewidth=1.0, alpha=0.9)
        body_low = min(row["Open"], row["Close"])
        body_h = max(abs(row["Close"] - row["Open"]), 1e-8)
        rect = Rectangle(
            (x[i] - candle_width / 2, body_low), candle_width, body_h,
            facecolor=color, edgecolor=color, linewidth=0.8, alpha=0.9
        )
        ax.add_patch(rect)

    ax.plot(x, plot_df["ema_20"].values, color="orange", linewidth=1.5, label="EMA20")
    ax.plot(x, plot_df["ema_50"].values, color="blue", linewidth=1.2, label="EMA50")
    ax.plot(x, plot_df["ema_100"].values, color="purple", linewidth=1.0, label="EMA100")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ax.set_title(f"{symbol_display} 15m 快照 — {ts}")
    ax.set_ylabel("价格")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()

    safe_name = symbol_display.replace("/", "_")
    fname = f"{safe_name}_snapshot_{ts}.png"
    path = os.path.join(save_dir, fname)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def notify_wechat(webhook_url: str, symbol_display: str, trend: str, entry: float,
                  stop: float, tp1: float, tp2: float, signal_time: str, chart_path: str = None) -> bool:
    """通过企业微信机器人发送 Markdown 通知"""
    if not webhook_url or not webhook_url.strip():
        print("  未配置 WECHAT_WEBHOOK_URL，跳过企业微信通知")
        return False
    direction_cn = "多头" if trend == "uptrend" else "空头"
    content = f"""## 交易建议
**品种:** {symbol_display}
**趋势方向:** {direction_cn}
**建议入场价:** {entry}
**止损价:** {stop}
**止盈1:** {tp1}
**止盈2:** {tp2}
**信号时间:** {signal_time}
"""
    if chart_path and os.path.isfile(chart_path):
        content += f"\n**K线图已保存:** `{chart_path}`"

    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        r = requests.post(webhook_url.strip(), json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("errcode") == 0:
            print("  企业微信通知已发送")
            return True
        print(f"  企业微信发送失败: {r.status_code} {r.text}")
        return False
    except Exception as e:
        print(f"  企业微信请求异常: {e}")
        return False


def run_live_check(symbols: list = None, api_key: str = None, wechat_webhook: str = None,
                   recent_bars_threshold: int = 5) -> list:
    """
    对指定品种拉取最新 200 根 15 分钟 K 线，检测是否有「当前」交易机会；
    若有则企业微信通知并保存 K 线图。
    :param symbols: 品种列表，如 ["eurusd","gbpusd"]
    :param api_key: TwelveData API Key
    :param wechat_webhook: 企业微信机器人 Webhook URL
    :param recent_bars_threshold: 仅当信号出现在最近 N 根 K 线内才视为当前机会
    :return: 本轮所有发出的建议列表
    """
    symbols = symbols or DEFAULT_SYMBOLS
    api_key = api_key or TWELVEDATA_API_KEY
    wechat_webhook = (wechat_webhook or WECHAT_WEBHOOK_URL or "").strip()

    if not api_key or api_key == "your_api_key_here":
        raise ValueError("请设置有效的 TWELVEDATA_API_KEY")

    run_time = datetime.now()
    save_root = os.path.join(LIVE_SUGGESTIONS_ROOT, run_time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(save_root, exist_ok=True)
    print(f"实时建议输出目录: {save_root}")

    suggestions = []
    for sym in symbols:
        try:
            print(f"\n检查品种: {SYMBOL_MAP.get(sym.lower(), sym)} ({sym})")
            df = load_latest_15min_bars(sym, api_key, n=1000)
            if len(df) < 160:
                print(f"  K 线不足 160 根，跳过")
                continue

            # 复用策略：用任意起止日期创建回测器，再替换为当前数据
            bt = CompleteStrategyBacktester(
                symbol=sym,
                start_date=(run_time - timedelta(days=1)).strftime("%Y-%m-%d"),
                end_date=run_time.strftime("%Y-%m-%d"),
                charts_output_dir=save_root,
            )
            bt.df = df
            signals = bt.find_signals()

            symbol_display = SYMBOL_MAP.get(sym.lower(), sym.upper())
            # 每次触发监控都保存该品种当时的 K 线快照（有无信号都保存）
            snap_path = plot_snapshot_chart(bt, symbol_display, save_root, show_bars=CHART_BARS)
            if snap_path:
                print(f"  [{symbol_display}] K线快照已保存: {snap_path}")

            # 只保留「当前」机会：信号出现在最近 recent_bars_threshold 根 K 线内
            n = len(bt.df)
            current_signals = [s for s in signals if s["idx"] >= n - recent_bars_threshold]
            if not current_signals:
                print(f"  无近期交易机会")
                continue

            for sig in current_signals:
                idx = sig["idx"]
                entry = sig["entry"]
                stop = sig["stop"]
                trend = sig["trend"]
                tp1, tp2 = bt.find_take_profit(idx, entry, stop, trend)
                signal_time = str(bt.df.index[idx])

                # 每笔交易建议单独保存带入场/止损/止盈标注的 K 线图
                chart_path = plot_suggestion_chart(bt, sig, symbol_display, save_root)
                if chart_path:
                    print(f"  本笔交易K线图已保存: {chart_path}")
                notify_wechat(wechat_webhook, symbol_display, trend, entry, stop, tp1, tp2, signal_time, chart_path)

                suggestions.append({
                    "symbol": symbol_display,
                    "symbol_key": sym,
                    "trend": trend,
                    "entry": entry,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "signal_time": signal_time,
                    "chart_path": chart_path,
                })
                print(f"  已发出建议: {symbol_display} {trend} 入场={entry} 止损={stop} 止盈1={tp1} 止盈2={tp2}")

        except Exception as e:
            print(f"  处理 {sym} 时出错: {e}")
            continue

    return suggestions


def run_backtest_mode(symbols: list, start_date: str, end_date: str,
                      api_key: str = None, output_dir: str = None,
                      save_charts: bool = True) -> dict:
    """
    回测模式：对指定品种在指定时间段内使用 EMA20 策略（15 分钟 K 线）进行回测。
    :param symbols: 品种列表，如 ["eurusd", "gbpusd"]
    :param start_date: 开始日期，格式 "YYYY-MM-DD"
    :param end_date: 结束日期，格式 "YYYY-MM-DD"
    :param api_key: TwelveData API Key（不传则用环境变量/默认）
    :param output_dir: 回测图表与汇总输出目录（不传则自动按时间建子目录）
    :param save_charts: 是否保存每笔交易的 K 线图
    :return: 各品种回测结果汇总 {"symbol": {"trades": [...], "summary": {...}}, ...}
    """
    api_key = api_key or TWELVEDATA_API_KEY
    if not api_key or api_key == "your_api_key_here":
        raise ValueError("回测需要有效的 TWELVEDATA_API_KEY")

    symbols = [s.lower() for s in symbols]
    for s in symbols:
        if s not in SYMBOL_MAP:
            raise ValueError(f"不支持的品种: {s}，支持: {list(SYMBOL_MAP.keys())}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = output_dir or os.path.join(BACKTEST_OUTPUTS_ROOT, f"ema20_{ts}")
    os.makedirs(out_root, exist_ok=True)
    charts_dir = os.path.join(out_root, "trade_charts")
    print(f"回测输出目录: {out_root}")
    print(f"回测区间: {start_date} ~ {end_date}，品种: {symbols}")
    print("使用 15 分钟 K 线，策略与实时信号一致（EMA20 回调）\n")

    all_results = {}
    for sym in symbols:
        symbol_display = SYMBOL_MAP.get(sym, sym.upper())
        try:
            print(f"---------- 回测品种: {symbol_display} ({sym}) ----------")
            bt = CompleteStrategyBacktester(
                symbol=sym,
                start_date=start_date,
                end_date=end_date,
                charts_output_dir=charts_dir,
            )
            bt.load_data(api_key=api_key, timeframe="15min")
            if len(bt.df) < 160:
                print(f"  K 线不足 160 根，跳过\n")
                all_results[sym] = {"trades": [], "summary": {"error": "数据不足"}, "signals_count": 0}
                continue

            signals = bt.find_signals()
            print(f"  信号数量: {len(signals)}")
            if not signals:
                all_results[sym] = {"trades": [], "summary": {}, "signals_count": 0}
                print()
                continue

            result = bt.run_backtest(signals, symbol_display=symbol_display, save_charts=save_charts)
            all_results[sym] = {
                "trades": result.get("trades", []),
                "summary": result.get("summary", {}),
                "signals_count": len(signals),
            }
            summary = result.get("summary", {})
            if summary:
                print(f"  交易次数: {summary.get('total_trades', 0)}")
                print(f"  胜率: {summary.get('win_rate', 0):.1f}%")
                print(f"  总盈亏: {summary.get('total_pnl', 0):.2f}")
                print(f"  期末资金: {summary.get('final_capital', 0):.2f}")
            print()
        except Exception as e:
            print(f"  回测失败: {e}\n")
            all_results[sym] = {"trades": [], "summary": {"error": str(e)}, "signals_count": 0}

    # 简要汇总
    print("========== 回测汇总 ==========")
    for sym, data in all_results.items():
        summary = data.get("summary", {})
        err = summary.get("error")
        if err:
            print(f"  {SYMBOL_MAP.get(sym, sym)}: 失败 - {err}")
        else:
            total = summary.get("total_trades", 0)
            wr = summary.get("win_rate", 0)
            pnl = summary.get("total_pnl", 0)
            print(f"  {SYMBOL_MAP.get(sym, sym)}: 交易 {total} 笔, 胜率 {wr:.1f}%, 总盈亏 {pnl:.2f}")
    print(f"\n图表目录: {charts_dir}")
    return all_results


def send_wecom_text(message: str) -> bool:
    """发送企业微信文本消息"""
    if WECHAT_WEBHOOK_URL == "WECHAT_WEBHOOK_URL":
        print(f"  [企业微信模拟文本] {message[:80]}...")
        return True
    payload = {"msgtype": "text", "text": {"content": message}}
    try:
        resp = requests.post(
            WECHAT_WEBHOOK_URL,
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

def main():
    import argparse
    parser = argparse.ArgumentParser(description="EMA20 回调策略 - 实时交易机会检测 / 回测（15 分钟 K 线）")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                        help=f"品种列表，默认: {DEFAULT_SYMBOLS}")
    parser.add_argument("--once", action="store_true", help="仅执行一次检测后退出")
    parser.add_argument("--interval", type=int, default=900,
                        help="轮询间隔秒数（默认 900，即 15 分钟一根 K 线）")
    parser.add_argument("--recent", type=int, default=5,
                        help="仅将最近 N 根 K 线内的信号视为当前机会（默认 5）")
    # 回测模式
    parser.add_argument("--backtest", action="store_true", help="启用回测模式：对指定品种在指定时间段回测")
    parser.add_argument("--start", type=str, default=None, metavar="YYYY-MM-DD",
                        help="回测开始日期（与 --backtest 配合）")
    parser.add_argument("--end", type=str, default=None, metavar="YYYY-MM-DD",
                        help="回测结束日期（与 --backtest 配合）")
    parser.add_argument("--no-charts", action="store_true", help="回测时不保存每笔交易 K 线图")
    args = parser.parse_args()

    # 回测模式：指定品种 + 时间区间
    if args.backtest:
        if not args.start or not args.end:
            print("回测模式需要指定 --start 和 --end，例如: --backtest --start 2026-01-01 --end 2026-03-01")
            sys.exit(1)
        run_backtest_mode(
            symbols=args.symbols,
            start_date=args.start,
            end_date=args.end,
            save_charts=not args.no_charts,
        )
        return

    webhook = WECHAT_WEBHOOK_URL
    if not webhook:
        print("提示: 未设置 WECHAT_WEBHOOK_URL，将不会发送企业微信通知。")
        print("      可在环境变量中设置，或在代码中修改 WECHAT_WEBHOOK_URL 默认值。")
    else:
        send_wecom_text(
            f"EMA20策略监控已启动\n"
            f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    if args.once:
        run_live_check(symbols=args.symbols, wechat_webhook=webhook, recent_bars_threshold=args.recent)
        return

    print(f"每 {args.interval} 秒检查一次，按 Ctrl+C 停止")
    while True:
        try:
            run_live_check(symbols=args.symbols, wechat_webhook=webhook, recent_bars_threshold=args.recent)
        except KeyboardInterrupt:
            print("\n已退出")
            break
        except Exception as e:
            print(f"本轮异常: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
