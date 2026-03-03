"""
H1/H2 外汇实时信号监控器 (5分钟K线版)
每5分钟扫描一次，发现新信号推送企业微信通知并保存本地K线图
数据源: Twelve Data (5分钟K线, 外汇/贵金属, 无需额外库)
策略: 双EMA排列 + RSI过滤 + H1/H2形态 + 分批止盈
"""

import sys
import requests
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
# ★ 配置区 — 填入你的企业微信机器人 Webhook 和 Twelve Data API Key
# ============================================================
# 企业微信 Webhook 获取方式：
# 1. 在企业微信群聊中点击「...」→「添加群机器人」
# 2. 设置机器人名称，获取 Webhook 地址
# 3. 将地址填入下方 WECOM_WEBHOOK_URL
WECOM_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128"   # 格式: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...

# Twelve Data API Key（在 twelvedata.com 注册后免费获取）
# 免费套餐：800次/天，8次/分钟，足够5品种5分钟轮询使用
TWELVEDATA_API_KEY = "61141e293ece4cad906e65413921b012"

# 监控品种 (Twelve Data 符号格式)
SYMBOLS = {
    'EURUSD': 'EUR/USD',   # 欧元/美元
    'USDJPY': 'USD/JPY',   # 美元/日元
    'GBPUSD': 'GBP/USD',   # 英镑/美元
    'XAUUSD': 'XAU/USD',   # 黄金/美元
    'XAGUSD': 'XAG/USD',   # 白银/美元
}

SCAN_INTERVAL_SECONDS = 300     # 扫描间隔：5分钟
LOOKBACK_BARS         = 500     # 获取最近500根5分钟K线（约41小时）
SIGNAL_FRESH_HOURS    = 2       # 只报告最近N小时内形成的信号
SIGNAL_CACHE_FILE     = "sent_signals.json"    # 已发送信号缓存
CHART_OUTPUT_DIR      = "signal_charts"        # K线图本地保存目录
# ============================================================


# ──────────────────────────────────────────
# 企业微信工具函数
# ──────────────────────────────────────────

def send_wecom_markdown(content: str) -> bool:
    """发送企业微信 Markdown 消息"""
    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print(f"  [企业微信模拟] {content[:80]}...")
        return True

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": content
        }
    }

    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get('errcode') != 0:
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

    payload = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }

    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get('errcode') != 0:
            print(f"  [企业微信发送失败] {result.get('errmsg', 'Unknown error')}")
            return False
        return True
    except Exception as e:
        print(f"  [企业微信发送失败] {e}")
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
    # 只保留最近7天记录，防止文件无限增长
    cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    filtered = {s for s in sent if s.split('|')[2][:10] >= cutoff}
    with open(SIGNAL_CACHE_FILE, 'w') as f:
        json.dump(list(filtered), f, indent=2)


# ──────────────────────────────────────────
# 数据加载（Twelve Data REST API 5分钟K线）
# ──────────────────────────────────────────

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"


def load_data(td_symbol: str, lookback_bars: int = LOOKBACK_BARS) -> pd.DataFrame | None:
    """
    使用 Twelve Data REST API 获取 5 分钟外汇/贵金属 K 线数据。
    td_symbol: Twelve Data 格式，如 'EUR/USD', 'XAU/USD'
    无需额外安装库，直接使用 requests。
    """
    params = {
        'symbol':     td_symbol,
        'interval':   '5min',
        'outputsize': lookback_bars,
        'apikey':     TWELVEDATA_API_KEY,
        'format':     'JSON',
        'order':      'ASC',   # 升序，最新在末尾
    }

    for attempt in range(3):
        try:
            resp = requests.get(TWELVEDATA_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            # 检查 API 错误
            if data.get('status') == 'error':
                code = data.get('code', '')
                msg  = data.get('message', '')
                print(f"  [Twelve Data 错误] code={code} {msg}")
                # 429 = 超频，稍等后重试
                if str(code) == '429' and attempt < 2:
                    time.sleep(15)
                    continue
                return None

            values = data.get('values')
            if not values:
                print(f"  [Twelve Data] {td_symbol} 无数据返回")
                return None
            break

        except requests.RequestException as e:
            print(f"  [Twelve Data 请求失败 attempt {attempt+1}] {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                return None

    # ── 解析为 DataFrame
    df = pd.DataFrame(values)
    df = df.rename(columns={
        'datetime': 'Datetime',
        'open':     'Open',
        'high':     'High',
        'low':      'Low',
        'close':    'Close',
    })

    # ── 设置 datetime 索引
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.set_index('Datetime').sort_index()
    df = df[~df.index.duplicated(keep='first')]

    if len(df) < 60:
        return None

    # ── 确保数值类型
    for col in ['Open', 'High', 'Low', 'Close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

    # ── 双 EMA
    df['ema_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['ema_50'] = df['Close'].ewm(span=50, adjust=False).mean()

    # ── RSI(14)
    delta = df['Close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df['rsi'] = df['rsi'].fillna(50)

    # ── K 线属性
    df['range']        = df['High'] - df['Low']
    df['body']         = abs(df['Close'] - df['Open'])
    df['body_ratio']   = df['body'] / df['range'].replace(0, np.nan)
    df['body_ratio']   = df['body_ratio'].fillna(0)
    df['is_trend_bar'] = df['body_ratio'] >= 0.5
    df['direction']    = np.where(df['Close'] > df['Open'], 1, -1)
    df['atr']          = df['range'].rolling(14).mean()

    return df


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
    """
    在 idx 处寻找 H1/L1 入场信号。
    5分钟周期优化：
      - 回看范围从 30 根缩短至 10 根（50分钟内，超出则形态太老）
      - 信号K线 j 不超过 idx（只认已收盘的K线）
    """
    if trend == 'uptrend':
        for i in range(idx - 1, max(idx - 10, 5), -1):
            if df['High'].iloc[i] < df['High'].iloc[i+1]:
                for j in range(i, min(i + 8, idx + 1)):   # j <= idx，只看已关闭K线
                    if df['High'].iloc[j] > df['High'].iloc[j-1]:
                        if df['Close'].iloc[j] > df['Open'].iloc[j]:
                            if df['body_ratio'].iloc[j] >= 0.3:
                                atr      = df['atr'].iloc[j]
                                slip     = atr * 0.01 if pd.notna(atr) else 0.0001
                                bars_ago = idx - j   # 信号K线距当前已过几根
                                return {
                                    'direction': 'long',
                                    'type':      'H1',
                                    'bar_time':  df.index[j].to_pydatetime(),
                                    'bar_idx':   j,
                                    'bars_ago':  bars_ago,
                                    'entry': df['High'].iloc[j] + slip,
                                    'stop':  df['Low'].iloc[j]  - slip,
                                    'close': df['Close'].iloc[j],
                                    'rsi':   df['rsi'].iloc[j],
                                    'atr':   atr if pd.notna(atr) else 0,
                                }
                break
    else:
        for i in range(idx - 1, max(idx - 10, 5), -1):
            if df['Low'].iloc[i] > df['Low'].iloc[i+1]:
                for j in range(i, min(i + 8, idx + 1)):   # j <= idx，只看已关闭K线
                    if df['Low'].iloc[j] < df['Low'].iloc[j-1]:
                        if df['Close'].iloc[j] < df['Open'].iloc[j]:
                            if df['body_ratio'].iloc[j] >= 0.3:
                                atr      = df['atr'].iloc[j]
                                slip     = atr * 0.01 if pd.notna(atr) else 0.0001
                                bars_ago = idx - j
                                return {
                                    'direction': 'short',
                                    'type':      'L1',
                                    'bar_time':  df.index[j].to_pydatetime(),
                                    'bar_idx':   j,
                                    'bars_ago':  bars_ago,
                                    'entry': df['Low'].iloc[j]  - slip,
                                    'stop':  df['High'].iloc[j] + slip,
                                    'close': df['Close'].iloc[j],
                                    'rsi':   df['rsi'].iloc[j],
                                    'atr':   atr if pd.notna(atr) else 0,
                                }
                break
    return None


# ──────────────────────────────────────────
# 本地K线图保存
# ──────────────────────────────────────────

def save_signal_chart(name: str, df: pd.DataFrame, signal: dict) -> str | None:
    """
    在信号触发时保存本地K线图（含 EMA20/50、RSI副图、入场/止损/TP标注）
    返回保存的文件路径，失败返回 None
    """
    try:
        import matplotlib
        matplotlib.use('Agg')   # 非交互后端，避免弹窗
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.patches as mpatches

        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        sig_idx  = signal['bar_idx']
        # 显示信号K线前后各 80 根
        start_i  = max(0, sig_idx - 80)
        end_i    = min(len(df), sig_idx + 20)
        chart_df = df.iloc[start_i:end_i].copy()

        # ── 价格精度
        decimals = 2 if signal['close'] > 100 else 5

        def fmt(p):
            return f"{p:.{decimals}f}"

        stop_dist = abs(signal['entry'] - signal['stop'])
        if signal['direction'] == 'long':
            tp1 = signal['entry'] + stop_dist * 1.0
            tp2 = signal['entry'] + stop_dist * 2.0
        else:
            tp1 = signal['entry'] - stop_dist * 1.0
            tp2 = signal['entry'] - stop_dist * 2.0

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(16, 9),
            gridspec_kw={'height_ratios': [3, 1]},
            sharex=True
        )
        fig.patch.set_facecolor('#1a1a2e')
        for ax in (ax1, ax2):
            ax.set_facecolor('#16213e')
            ax.tick_params(colors='#e0e0e0')
            ax.spines[:].set_color('#444466')

        # ── 绘制K线（蜡烛图）
        for i in range(len(chart_df)):
            dt      = chart_df.index[i]
            op      = chart_df['Open'].iloc[i]
            hi      = chart_df['High'].iloc[i]
            lo      = chart_df['Low'].iloc[i]
            cl      = chart_df['Close'].iloc[i]
            is_bull = cl >= op
            color   = '#26a69a' if is_bull else '#ef5350'   # 绿涨红跌
            ax1.plot([dt, dt], [lo, hi], color=color, linewidth=0.8, zorder=2)
            body_lo = min(op, cl)
            body_hi = max(op, cl)
            body_h  = max(body_hi - body_lo, (hi - lo) * 0.002)
            rect = mpatches.Rectangle(
                (mdates.date2num(dt) - 0.0015, body_lo),
                0.003, body_h,
                facecolor=color, edgecolor=color, alpha=0.9, zorder=3
            )
            ax1.add_patch(rect)

        # ── EMA 线
        ax1.plot(chart_df.index, chart_df['ema_20'], color='#42a5f5',
                 linewidth=1.3, label='EMA20', zorder=4)
        ax1.plot(chart_df.index, chart_df['ema_50'], color='#ffa726',
                 linewidth=1.3, label='EMA50', zorder=4)

        # ── 水平线：入场 / 止损 / TP1 / TP2
        line_cfg = [
            (signal['entry'], '#42a5f5', '--', f"入场 {fmt(signal['entry'])}"),
            (signal['stop'],  '#ef5350', '--', f"止损 {fmt(signal['stop'])}"),
            (tp1,             '#ab47bc', ':',  f"TP1  {fmt(tp1)}"),
            (tp2,             '#66bb6a', ':',  f"TP2  {fmt(tp2)}"),
        ]
        for price, clr, ls, lbl in line_cfg:
            ax1.axhline(price, color=clr, linestyle=ls, linewidth=1.0,
                        alpha=0.85, label=lbl, zorder=5)

        # ── 信号K线标注箭头
        sig_dt    = signal['bar_time']
        arrow_y   = signal['stop'] - stop_dist * 0.5 if signal['direction'] == 'long' \
                    else signal['stop'] + stop_dist * 0.5
        arrow_dir = '^' if signal['direction'] == 'long' else 'v'
        ax1.scatter([sig_dt], [signal['entry']], marker=arrow_dir,
                    color='#ffeb3b', s=200, zorder=7, edgecolors='white', linewidths=1.5)
        ax1.annotate(
            f"{signal['type']}\n{fmt(signal['entry'])}",
            xy=(sig_dt, signal['entry']),
            xytext=(15, 30 if signal['direction'] == 'long' else -45),
            textcoords='offset points',
            color='#ffeb3b',
            fontsize=8,
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e', alpha=0.8),
            arrowprops=dict(arrowstyle='->', color='#ffeb3b'),
            zorder=8
        )

        dir_label = "做多 LONG ▲" if signal['direction'] == 'long' else "做空 SHORT ▼"
        sig_time_str = signal['bar_time'].strftime('%Y-%m-%d %H:%M')
        ax1.set_title(
            f"{name}  [5分钟]   {dir_label}   信号时间: {sig_time_str}   "
            f"RSI: {signal['rsi']:.1f}",
            color='#e0e0e0', fontsize=12, fontweight='bold', pad=10
        )
        ax1.set_ylabel('价格', color='#e0e0e0', fontsize=10)
        ax1.legend(loc='upper left', fontsize=8, facecolor='#1a1a2e',
                   labelcolor='#e0e0e0', framealpha=0.8)
        ax1.grid(True, alpha=0.15, color='#444466')
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))

        # ── RSI 副图
        ax2.plot(chart_df.index, chart_df['rsi'], color='#ce93d8', linewidth=1.2, label='RSI(14)')
        ax2.axhline(70, color='#ef5350', linestyle='--', linewidth=0.8, alpha=0.6)
        ax2.axhline(30, color='#26a69a', linestyle='--', linewidth=0.8, alpha=0.6)
        ax2.axhline(50, color='#888888', linestyle='--', linewidth=0.5, alpha=0.4)
        ax2.fill_between(chart_df.index, 30, 70, alpha=0.06, color='#888888')
        # 标记信号时刻RSI
        try:
            rsi_at_sig = chart_df.loc[sig_dt, 'rsi'] if sig_dt in chart_df.index \
                         else chart_df['rsi'].iloc[-1]
            ax2.scatter([sig_dt], [rsi_at_sig], color='#ffeb3b', s=60, zorder=5)
        except Exception:
            pass
        ax2.set_ylim(0, 100)
        ax2.set_ylabel('RSI', color='#e0e0e0', fontsize=9)
        ax2.set_xlabel('时间 (5分钟K线)', color='#e0e0e0', fontsize=9)
        ax2.legend(loc='upper left', fontsize=8, facecolor='#1a1a2e',
                   labelcolor='#e0e0e0', framealpha=0.8)
        ax2.grid(True, alpha=0.15, color='#444466')
        ax2.tick_params(axis='x', rotation=20)

        plt.tight_layout(rect=[0, 0, 1, 0.97])

        # ── 保存
        os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{name}_{signal['direction']}_{signal['type']}_{ts}.png"
        filepath = os.path.join(CHART_OUTPUT_DIR, filename)
        plt.savefig(filepath, dpi=130, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close(fig)
        return filepath

    except Exception as e:
        print(f"  [图表保存失败] {e}")
        return None


# ──────────────────────────────────────────
# 企业微信消息格式化（Markdown）
# ──────────────────────────────────────────

def format_signal_markdown(name: str, signal: dict, scan_time: str) -> str:
    """生成企业微信 Markdown 格式消息"""
    stop_dist = abs(signal['entry'] - signal['stop'])
    rr = 2.0

    if signal['direction'] == 'long':
        emoji     = "📈"
        label     = "做多 LONG"
        color_tag = "<font color=\"info\">多单</font>"
        tp1 = signal['entry'] + stop_dist * 1.0
        tp2 = signal['entry'] + stop_dist * rr
    else:
        emoji     = "📉"
        label     = "做空 SHORT"
        color_tag = "<font color=\"warning\">空单</font>"
        tp1 = signal['entry'] - stop_dist * 1.0
        tp2 = signal['entry'] - stop_dist * rr

    # 根据价格大小自动选择小数位数
    decimals = 2 if signal['close'] > 100 else 5

    def fmt_price(price):
        return f"{price:.{decimals}f}"

    bar_time_str  = signal['bar_time'].strftime('%Y-%m-%d %H:%M')
    bars_ago      = signal.get('bars_ago', 0)
    current_price = signal.get('current_price', signal['close'])

    # 当前价距入场价的距离（百分比）
    dist_pct = abs(signal['entry'] - current_price) / current_price * 100
    if signal['direction'] == 'long':
        dist_label = f"距入场还差 {fmt_price(signal['entry'] - current_price)} ({dist_pct:.3f}%)"
    else:
        dist_label = f"距入场还差 {fmt_price(current_price - signal['entry'])} ({dist_pct:.3f}%)"

    # 构建 Markdown 消息
    markdown = f"""## 🔔 H1/H2 新信号 (5分钟) - {name}

> **交易方向**: {emoji} {label} {color_tag}
> **信号类型**: {signal['type']}
> **信号K线**: {bar_time_str}（{bars_ago * 5} 分钟前）

### 📊 交易参数
> 💵 **当前价**: <font color=\"comment\">{fmt_price(current_price)}</font>　{dist_label}
> 🎯 **入场价**: <font color=\"info\">{fmt_price(signal['entry'])}</font>　←挂停损买/卖单
> 🛡 **止损价**: <font color=\"warning\">{fmt_price(signal['stop'])}</font>
> 💰 **TP1 (1:1)**: <font color=\"comment\">{fmt_price(tp1)}</font>
> 🚀 **TP2 (2:1)**: <font color=\"comment\">{fmt_price(tp2)}</font>

### 📈 技术指标
> 📊 **RSI**: {signal['rsi']:.1f}
> 📏 **ATR**: {fmt_price(signal['atr'])}
> 📉 **信号K线收盘**: {fmt_price(signal['close'])}

⏰ 扫描时间: {scan_time}"""

    return markdown


# ──────────────────────────────────────────
# 主扫描循环
# ──────────────────────────────────────────

def scan_once(sent_signals: set) -> tuple[set, int]:
    """扫描一次所有品种，返回更新后的缓存和新信号数"""
    now_str   = datetime.now().strftime('%Y-%m-%d %H:%M')
    cutoff_dt = datetime.now() - timedelta(hours=SIGNAL_FRESH_HOURS)
    new_count = 0

    for name, ts_symbol in SYMBOLS.items():
        df = load_data(ts_symbol)
        if df is None:
            print(f"  {name}: 数据加载失败，跳过")
            continue

        print(f"  {name}: 已加载 {len(df)} 根5分钟K线")

        # 只在最近5根K线中扫描（防止反复报旧信号）
        scan_start   = max(50, len(df) - 5)
        last_sig_idx = -10

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

            # ── 【5分钟核心过滤】入场价有效性检查 ──────────────────
            # 当前最新收盘价（最后一根已收盘K线）
            current_price = df['Close'].iloc[-1]
            decimals = 2 if current_price > 100 else 5

            if signal['direction'] == 'long' and current_price >= signal['entry']:
                # 价格已突破入场位，机会错过
                print(f"  ⏩ {name} 多头入场价已被突破，跳过 "
                      f"(当前:{current_price:.{decimals}f} >= 入场:{signal['entry']:.{decimals}f})")
                continue
            if signal['direction'] == 'short' and current_price <= signal['entry']:
                # 价格已跌破入场位，机会错过
                print(f"  ⏩ {name} 空头入场价已被突破，跳过 "
                      f"(当前:{current_price:.{decimals}f} <= 入场:{signal['entry']:.{decimals}f})")
                continue
            # ──────────────────────────────────────────────────────

            # 把当前价存入 signal，供消息格式化使用
            signal['current_price'] = current_price

            # 只处理"新鲜"信号（最近 SIGNAL_FRESH_HOURS 小时内）
            bar_time = signal['bar_time']
            # 统一去掉时区信息再比较
            if bar_time.tzinfo is not None:
                bar_time_naive = bar_time.replace(tzinfo=None)
            else:
                bar_time_naive = bar_time
            if bar_time_naive < cutoff_dt:
                continue

            # 生成唯一 ID（精确到分钟）
            sig_id = f"{name}|{signal['direction']}|{bar_time_naive.strftime('%Y-%m-%d %H:%M')}"
            if sig_id in sent_signals:
                continue  # 已推送过

            # 保存本地K线图
            chart_path = save_signal_chart(name, df, signal)
            if chart_path:
                print(f"  📊 K线图已保存: {chart_path}")

            # 推送企业微信
            message = format_signal_markdown(name, signal, now_str)

            print(f"\n  ✅ 新信号: {name} {signal['direction'].upper()} "
                  f"@ {signal['entry']:.5f}  止损: {signal['stop']:.5f}  "
                  f"当前:{current_price:.5f}")

            if send_wecom_markdown(message):
                sent_signals.add(sig_id)
                save_sent_signals(sent_signals)
                new_count += 1
            else:
                print(f"  ⚠️  推送失败，下次重试")

    return sent_signals, new_count


def main():
    print("=" * 60)
    print("  H1/H2 外汇实时信号监控器 (5分钟K线 · 企业微信版)")
    print(f"  数据源: Twelve Data (twelvedata.com)")
    print(f"  监控品种: {', '.join(SYMBOLS.keys())}")
    print(f"  扫描间隔: {SCAN_INTERVAL_SECONDS // 60} 分钟")
    print(f"  信号新鲜度: 最近 {SIGNAL_FRESH_HOURS} 小时")
    print(f"  策略: 双EMA排列 + RSI过滤 + H1/H2形态 + 分批止盈")
    print(f"  通知方式: 企业微信群机器人 (Markdown 格式)")
    print(f"  K线图目录: {CHART_OUTPUT_DIR}/")
    print("=" * 60)

    # 检查 API Key
    if TWELVEDATA_API_KEY == "YOUR_TWELVEDATA_API_KEY":
        print("\n❌ 请在脚本顶部填入 TWELVEDATA_API_KEY")
        print("   注册地址: https://twelvedata.com  (免费套餐即可)")
        return

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("\n⚠️  缺少依赖库: matplotlib（K线图功能不可用）")
        print("   请运行: pip install matplotlib")

    if WECOM_WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print("\n⚠️  注意: 企业微信 Webhook 未配置，将以模拟模式运行")
        print("   请在脚本顶部填入 WECOM_WEBHOOK_URL\n")
    else:
        send_wecom_text(
            f"✅ H1/H2 监控器已启动 (5分钟K线)\n"
            f"监控品种: {', '.join(SYMBOLS.keys())}\n"
            f"扫描间隔: {SCAN_INTERVAL_SECONDS // 60} 分钟\n"
            f"策略: 双EMA排列 + RSI过滤 + H1/H2形态\n"
            f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)
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
            if WECOM_WEBHOOK_URL != "YOUR_WEBHOOK_URL":
                send_wecom_text("⛔ H1/H2 监控器已停止")
            break
        except Exception as e:
            print(f"  [扫描异常] {e}")
            if WECOM_WEBHOOK_URL != "YOUR_WEBHOOK_URL":
                send_wecom_text(f"⚠️ 监控器异常: {e}")

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
