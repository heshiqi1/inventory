"""
H1/H2 外汇实时信号监控器
每1小时扫描一次，发现新信号推送 Telegram 通知
数据源: Stooq (免费无限速, 日线)
策略: 双EMA排列 + RSI过滤 + H1/H2形态 + 分批止盈
"""

import sys
import requests
import io
import time
import json
import os
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# 修复 Windows 控制台 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ============================================================
# ★ 配置区 — 填入你的 Telegram 信息后即可运行
# ============================================================
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"   # 向 @BotFather 发送 /newbot 获取
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID"     # 向 @userinfobot 发送任意消息获取

# 监控品种 (Stooq 符号)
SYMBOLS = {
    'EURUSD': 'eurusd',   # 欧元/美元
    'USDJPY': 'usdjpy',   # 美元/日元
    'GBPUSD': 'gbpusd',   # 英镑/美元
    'XAUUSD': 'xauusd',   # 黄金/美元
    'XAGUSD': 'xagusd',   # 白银/美元
}

SCAN_INTERVAL_SECONDS = 3600   # 扫描间隔：1小时
LOOKBACK_DAYS         = 150    # 获取最近150天数据 (确保指标计算充足)
SIGNAL_FRESH_DAYS     = 2      # 只报告最近N天内形成的信号
SIGNAL_CACHE_FILE     = "sent_signals.json"   # 已发送信号缓存
# ============================================================


# ──────────────────────────────────────────
# Telegram 工具函数
# ──────────────────────────────────────────

def send_telegram(message: str, silent: bool = False) -> bool:
    """发送 Telegram 消息"""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        print(f"  [Telegram 模拟] {message[:80]}...")
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"  [Telegram 发送失败] {e}")
        return False


# ──────────────────────────────────────────
# 信号缓存：防止同一信号重复推送
# ──────────────────────────────────────────

def load_sent_signals() -> set:
    if os.path.exists(SIGNAL_CACHE_FILE):
        try:
            with open(SIGNAL_CACHE_FILE, 'r') as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_sent_signals(sent: set):
    # 只保留最近30天记录，防止文件无限增长
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    filtered = {s for s in sent if s.split('|')[2] >= cutoff}
    with open(SIGNAL_CACHE_FILE, 'w') as f:
        json.dump(list(filtered), f, indent=2)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────

def load_data(symbol: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """从 Stooq 加载近 N 天日线数据并计算所有指标"""
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)
    d1 = start_date.strftime('%Y%m%d')
    d2 = end_date.strftime('%Y%m%d')
    url = f"https://stooq.com/q/d/l/?s={symbol}&d1={d1}&d2={d2}&i=d"

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=20,
                                headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt < 2:
                time.sleep(3)
            else:
                return None

    try:
        df = pd.read_csv(io.StringIO(resp.text), parse_dates=['Date'], index_col='Date')
        df = df.sort_index()

        if len(df) < 60:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]

        # 双 EMA
        df['ema_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['Close'].ewm(span=50, adjust=False).mean()

        # RSI(14)
        delta = df['Close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        df['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        df['rsi'] = df['rsi'].fillna(50)

        # K 线属性
        df['range']      = df['High'] - df['Low']
        df['body']       = abs(df['Close'] - df['Open'])
        df['body_ratio'] = df['body'] / df['range'].replace(0, np.nan)
        df['body_ratio'] = df['body_ratio'].fillna(0)
        df['is_trend_bar'] = df['body_ratio'] >= 0.5
        df['direction']    = np.where(df['Close'] > df['Open'], 1, -1)
        df['atr']          = df['range'].rolling(14).mean()

        return df

    except Exception:
        return None


# ──────────────────────────────────────────
# 趋势 & 信号检测（与回测策略完全一致）
# ──────────────────────────────────────────

def detect_trend(df: pd.DataFrame, idx: int) -> str | None:
    """双EMA排列趋势检测"""
    if idx < 50:
        return None
    ema_20 = df['ema_20'].iloc[idx]
    ema_50 = df['ema_50'].iloc[idx]
    close  = df['Close'].iloc[idx]
    slope  = (df['ema_20'].iloc[idx] - df['ema_20'].iloc[idx-5]) / df['ema_20'].iloc[idx-5]

    if close > ema_20 and ema_20 > ema_50 and slope > 0.0003:
        return 'uptrend'
    if close < ema_20 and ema_20 < ema_50 and slope < -0.0003:
        return 'downtrend'
    return None


def is_momentum_confirmed(df: pd.DataFrame, idx: int, trend: str) -> bool:
    """宽松动量确认：连续2根趋势K线 OR 近3根收盘有方向性"""
    if idx < 5:
        return False
    recent = df.iloc[idx-4:idx]
    dirs   = recent['direction'].values
    tbars  = recent['is_trend_bar'].values
    if len(dirs) >= 2 and dirs[-1] == dirs[-2] and tbars[-1] and tbars[-2]:
        return True
    closes = df['Close'].iloc[idx-3:idx].values
    if trend == 'uptrend'   and closes[-1] > closes[0]:
        return True
    if trend == 'downtrend' and closes[-1] < closes[0]:
        return True
    return False


def find_signal(df: pd.DataFrame, idx: int, trend: str) -> dict | None:
    """在 idx 处寻找 H1/L1 入场信号"""
    if trend == 'uptrend':
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if df['High'].iloc[i] < df['High'].iloc[i+1]:
                for j in range(i, min(i + 15, len(df) - 1)):
                    if df['High'].iloc[j] > df['High'].iloc[j-1]:
                        if df['Close'].iloc[j] > df['Open'].iloc[j]:
                            if df['body_ratio'].iloc[j] >= 0.3:
                                atr  = df['atr'].iloc[j]
                                slip = atr * 0.01 if pd.notna(atr) else 0.0001
                                return {
                                    'direction': 'long',
                                    'type': 'H1',
                                    'bar_date': df.index[j].date(),
                                    'entry': df['High'].iloc[j] + slip,
                                    'stop':  df['Low'].iloc[j]  - slip,
                                    'close': df['Close'].iloc[j],
                                    'rsi':   df['rsi'].iloc[j],
                                    'atr':   atr if pd.notna(atr) else 0,
                                }
                break
    else:
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if df['Low'].iloc[i] > df['Low'].iloc[i+1]:
                for j in range(i, min(i + 15, len(df) - 1)):
                    if df['Low'].iloc[j] < df['Low'].iloc[j-1]:
                        if df['Close'].iloc[j] < df['Open'].iloc[j]:
                            if df['body_ratio'].iloc[j] >= 0.3:
                                atr  = df['atr'].iloc[j]
                                slip = atr * 0.01 if pd.notna(atr) else 0.0001
                                return {
                                    'direction': 'short',
                                    'type': 'L1',
                                    'bar_date': df.index[j].date(),
                                    'entry': df['Low'].iloc[j]  - slip,
                                    'stop':  df['High'].iloc[j] + slip,
                                    'close': df['Close'].iloc[j],
                                    'rsi':   df['rsi'].iloc[j],
                                    'atr':   atr if pd.notna(atr) else 0,
                                }
                break
    return None


# ──────────────────────────────────────────
# Telegram 消息格式化
# ──────────────────────────────────────────

def format_signal_message(name: str, signal: dict, scan_time: str) -> str:
    stop_dist = abs(signal['entry'] - signal['stop'])
    rr = 2.0

    if signal['direction'] == 'long':
        emoji  = "📈"
        label  = "LONG (做多)"
        tp1    = signal['entry'] + stop_dist * 1.0
        tp2    = signal['entry'] + stop_dist * rr
    else:
        emoji  = "📉"
        label  = "SHORT (做空)"
        tp1    = signal['entry'] - stop_dist * 1.0
        tp2    = signal['entry'] - stop_dist * rr

    # 根据价格大小自动选择小数位数
    decimals = 2 if signal['close'] > 100 else 5
    fmt = f"{{:.{decimals}f}}"

    return (
        f"🔔 <b>H1/H2 新信号</b>\n"
        f"{'━'*28}\n"
        f"📌 品种: <b>{name}</b>  {emoji} <b>{label}</b>\n"
        f"📅 信号日期: {signal['bar_date']}\n"
        f"{'━'*28}\n"
        f"🎯 入场价: <b>{fmt.format(signal['entry'])}</b>\n"
        f"🛡 止损:   {fmt.format(signal['stop'])}\n"
        f"💰 TP1 (1:1): {fmt.format(tp1)}\n"
        f"🚀 TP2 (2:1): {fmt.format(tp2)}\n"
        f"{'━'*28}\n"
        f"📊 RSI: {signal['rsi']:.1f}  |  ATR: {fmt.format(signal['atr'])}\n"
        f"💵 当前收盘: {fmt.format(signal['close'])}\n"
        f"⏰ 扫描时间: {scan_time}"
    )


# ──────────────────────────────────────────
# 主扫描循环
# ──────────────────────────────────────────

def scan_once(sent_signals: set) -> tuple[set, int]:
    """扫描一次所有品种，返回更新后的缓存和新信号数"""
    now_str   = datetime.now().strftime('%Y-%m-%d %H:%M')
    cutoff_dt = datetime.now().date() - timedelta(days=SIGNAL_FRESH_DAYS)
    new_count = 0

    for name, symbol in SYMBOLS.items():
        df = load_data(symbol)
        if df is None:
            print(f"  {name}: 数据加载失败，跳过")
            continue

        # 只在最近5根K线中扫描（防止反复报旧信号）
        scan_start    = max(50, len(df) - 5)
        last_sig_idx  = -10

        for idx in range(scan_start, len(df) - 1):
            trend = detect_trend(df, idx)
            if trend is None:
                continue
            if not is_momentum_confirmed(df, idx, trend):
                continue

            rsi = df['rsi'].iloc[idx]
            if trend == 'uptrend'   and rsi > 70:
                continue
            if trend == 'downtrend' and rsi < 30:
                continue
            if idx - last_sig_idx < 5:
                continue

            signal = find_signal(df, idx, trend)
            if signal is None:
                continue

            last_sig_idx = idx

            # 只处理"新鲜"信号
            if signal['bar_date'] < cutoff_dt:
                continue

            # 生成唯一 ID
            sig_id = f"{name}|{signal['direction']}|{signal['bar_date']}"
            if sig_id in sent_signals:
                continue  # 已推送过

            # 推送 Telegram
            msg = format_signal_message(name, signal, now_str)
            print(f"\n  ✅ 新信号: {name} {signal['direction'].upper()} "
                  f"@ {signal['entry']:.5f}  止损: {signal['stop']:.5f}")

            if send_telegram(msg):
                sent_signals.add(sig_id)
                save_sent_signals(sent_signals)
                new_count += 1
            else:
                print(f"  ⚠️  推送失败，下次重试")

    return sent_signals, new_count


def main():
    print("=" * 52)
    print("  H1/H2 外汇实时信号监控器")
    print(f"  监控品种: {', '.join(SYMBOLS.keys())}")
    print(f"  扫描间隔: {SCAN_INTERVAL_SECONDS // 60} 分钟")
    print(f"  信号新鲜度: 最近 {SIGNAL_FRESH_DAYS} 天")
    print(f"  策略: 双EMA排列 + RSI过滤 + H1/H2形态")
    print("=" * 52)

    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("\n⚠️  注意: Telegram 未配置，将以模拟模式运行")
        print("   请在脚本顶部填入 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID\n")
    else:
        send_telegram(
            "✅ <b>H1/H2 监控器已启动</b>\n"
            f"监控品种: {', '.join(SYMBOLS.keys())}\n"
            f"扫描间隔: {SCAN_INTERVAL_SECONDS // 60} 分钟\n"
            f"策略: 双EMA排列 + RSI过滤 + H1/H2形态\n"
            f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    sent_signals = load_sent_signals()
    scan_count   = 0

    while True:
        scan_count += 1
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[第{scan_count}次扫描] {now_str}")
        print("-" * 40)

        try:
            sent_signals, new_count = scan_once(sent_signals)
            if new_count == 0:
                print("  本次扫描：无新信号")
            else:
                print(f"  本次扫描：推送 {new_count} 个新信号 ✅")

        except KeyboardInterrupt:
            print("\n\n监控器已手动停止 (Ctrl+C)")
            if TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN":
                send_telegram("⛔ H1/H2 监控器已停止")
            break
        except Exception as e:
            print(f"  [扫描异常] {e}")
            if TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN":
                send_telegram(f"⚠️ 监控器异常: {e}")

        next_scan = datetime.now() + timedelta(seconds=SCAN_INTERVAL_SECONDS)
        print(f"\n下次扫描: {next_scan.strftime('%Y-%m-%d %H:%M:%S')}")
        print("  (按 Ctrl+C 停止监控)")

        # 等待期间每分钟打印一次倒计时
        for remaining in range(SCAN_INTERVAL_SECONDS, 0, -60):
            time.sleep(min(60, remaining))
            if remaining > 60:
                print(f"  ⏳ 距下次扫描还有 {remaining // 60} 分钟...", end='\r')


if __name__ == "__main__":
    main()
