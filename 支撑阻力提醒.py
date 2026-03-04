import ccxt
import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime
from collections import deque

"""
增加微信企业启动通知，每次支撑阻力机会出现的时候通知机会：品种、价格、趋势方向等等重要信息，支撑多品种同时监控提醒，并且使用twelvedata数据源
"""

# ==================== 配置参数 ====================
SYMBOLS = ['JPY/USDT', 'EUR/USDT', 'BTC/USDT']  # 交易对列表（支持多品种）
TIMEFRAMES = ['5m', '15m', '1h']         # 监控周期
EMA_PERIODS = [20, 50, 100]              # EMA周期
SWING_WINDOW = 2                         # 摆动点识别窗口（左右各几根）
LEVEL_MERGE_THRESHOLD = 0.001             # 水平位合并阈值（0.1%）
NEAR_THRESHOLD = 0.001                    # 价格接近阈值（0.1%）
COOLDOWN_SECONDS = 900                    # 同一位置重复提醒冷却时间（秒）
WEWORK_WEBHOOK_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128'  # 企业微信机器人Webhook地址

# ==================== 数据获取模块 ====================
class DataFetcher:
    def __init__(self, symbol, timeframes, limit=200):
        self.symbol = symbol
        self.timeframes = timeframes
        self.limit = limit
        self.exchange = ccxt.binance()
        self.data = {tf: deque(maxlen=limit) for tf in timeframes}  # 存储K线数据
        self.ema = {tf: {} for tf in timeframes}                    # 存储最新EMA值

    def fetch_klines(self, timeframe):
        """获取指定周期K线数据，返回DataFrame"""
        klines = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=self.limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def calc_ema(self, df, periods):
        """计算EMA并返回最新值和前值"""
        for p in periods:
            col = f'EMA{p}'
            df[col] = df['close'].ewm(span=p, adjust=False).mean()
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None
        ema_dict = {}
        for p in periods:
            col = f'EMA{p}'
            ema_dict[col] = last[col]
            ema_dict[f'prev_{col}'] = prev[col] if prev is not None else None
        return ema_dict

    def update_all(self):
        """更新所有周期数据，并计算EMA"""
        for tf in self.timeframes:
            df = self.fetch_klines(tf)
            # 检测是否有新K线（通过最后一条时间戳）
            if len(self.data[tf]) == 0 or df['timestamp'].iloc[-1] != self.data[tf][-1]['timestamp']:
                self.data[tf] = df.to_dict('records')
                self.ema[tf] = self.calc_ema(df, EMA_PERIODS)

    def get_latest_price(self):
        """获取当前最新成交价"""
        ticker = self.exchange.fetch_ticker(self.symbol)
        return ticker['last']

# ==================== 趋势判断模块 ====================
def check_trend(ema_dict):
    """
    根据EMA和斜率判断单周期趋势
    返回: 'bull', 'bear' 或 None
    """
    cond_bull = (ema_dict['EMA20'] > ema_dict['EMA50'] > ema_dict['EMA100'])
    cond_bull_slope = (ema_dict['EMA20'] > ema_dict['prev_EMA20'] and
                       ema_dict['EMA50'] > ema_dict['prev_EMA50'] and
                       ema_dict['EMA100'] > ema_dict['prev_EMA100'])
    if cond_bull and cond_bull_slope:
        return 'bull'

    cond_bear = (ema_dict['EMA20'] < ema_dict['EMA50'] < ema_dict['EMA100'])
    cond_bear_slope = (ema_dict['EMA20'] < ema_dict['prev_EMA20'] and
                       ema_dict['EMA50'] < ema_dict['prev_EMA50'] and
                       ema_dict['EMA100'] < ema_dict['prev_EMA100'])
    if cond_bear and cond_bear_slope:
        return 'bear'
    return None

def get_overall_trend(fetcher):
    """综合1小时和15分钟趋势，返回 'bull'/'bear'/'neutral'"""
    trend_1h = check_trend(fetcher.ema['1h'])
    trend_15m = check_trend(fetcher.ema['15m'])
    if trend_1h == 'bull' and trend_15m == 'bull':
        return 'bull'
    elif trend_1h == 'bear' and trend_15m == 'bear':
        return 'bear'
    else:
        return 'neutral'

# ==================== 支撑阻力计算模块 ====================
def find_swing_points(df, window=SWING_WINDOW):
    """
    识别摆动高点和低点
    返回 (highs, lows)，每个元素为 (index, price)
    """
    highs = []
    lows = []
    for i in range(window, len(df)-window):
        # 摆动高点
        if all(df['high'].iloc[i] > df['high'].iloc[i-j] for j in range(1, window+1)) and \
           all(df['high'].iloc[i] > df['high'].iloc[i+j] for j in range(1, window+1)):
            highs.append((i, df['high'].iloc[i]))
        # 摆动低点
        if all(df['low'].iloc[i] < df['low'].iloc[i-j] for j in range(1, window+1)) and \
           all(df['low'].iloc[i] < df['low'].iloc[i+j] for j in range(1, window+1)):
            lows.append((i, df['low'].iloc[i]))
    return highs, lows

def trend_lines(df, highs, lows):
    """
    计算上升趋势线（支撑）和下降趋势线（阻力）
    返回 (uptrend_support, downtrend_resistance) 当前值，若无则None
    """
    support = None
    resistance = None
    current_idx = len(df) - 1

    # 上升趋势线：最近两个依次抬高的低点
    if len(lows) >= 2:
        l1_idx, l1_price = lows[-1]
        # 向前找第一个价格低于l1_price的低点
        for i in range(len(lows)-2, -1, -1):
            l0_idx, l0_price = lows[i]
            if l0_price < l1_price:
                k = (l1_price - l0_price) / (l1_idx - l0_idx)
                support = l0_price + k * (current_idx - l0_idx)
                break

    # 下降趋势线：最近两个依次降低的高点
    if len(highs) >= 2:
        h1_idx, h1_price = highs[-1]
        for i in range(len(highs)-2, -1, -1):
            h0_idx, h0_price = highs[i]
            if h0_price > h1_price:
                k = (h1_price - h0_price) / (h1_idx - h0_idx)
                resistance = h0_price + k * (current_idx - h0_idx)
                break

    return support, resistance

def horizontal_levels(highs, lows, n=5, merge_threshold=LEVEL_MERGE_THRESHOLD):
    """
    获取最近n个摆动点，合并相近价格，返回水平位列表（带类型）
    每个元素: {'name': '水平支撑/阻力', 'type': 'support'/'resistance', 'value': price}
    """
    recent_highs = [price for _, price in highs[-n:]]
    recent_lows = [price for _, price in lows[-n:]]
    levels = []
    for price in recent_highs:
        levels.append({'name': f'水平阻力', 'type': 'resistance', 'value': price})
    for price in recent_lows:
        levels.append({'name': f'水平支撑', 'type': 'support', 'value': price})
    # 合并相近价格（简化：相同价格只保留一个，类型合并为支撑/阻力？实际可能既是支撑又是阻力，我们保留为两者）
    merged = []
    seen = set()
    for level in sorted(levels, key=lambda x: x['value']):
        if not seen or not any(abs(level['value'] - v) < merge_threshold * level['value'] for v in seen):
            merged.append(level)
            seen.add(level['value'])
    return merged

def last_breakout(df, highs, lows):
    """
    检测最新一根K线是否突破前一个摆动点，返回突破价位和类型
    返回列表，可能包含0-2个元素
    """
    breakouts = []
    if len(highs) > 0:
        prev_high = highs[-1][1]
        if df['close'].iloc[-1] > prev_high:
            breakouts.append({'name': '向上突破位', 'type': 'resistance', 'value': prev_high})
    if len(lows) > 0:
        prev_low = lows[-1][1]
        if df['close'].iloc[-1] < prev_low:
            breakouts.append({'name': '向下突破位', 'type': 'support', 'value': prev_low})
    return breakouts

def last_extremes(highs, lows):
    """最近一个摆动高点和低点"""
    levels = []
    if highs:
        levels.append({'name': '最近高点', 'type': 'resistance', 'value': highs[-1][1]})
    if lows:
        levels.append({'name': '最近低点', 'type': 'support', 'value': lows[-1][1]})
    return levels

def fibonacci_levels(highs, lows, df):
    """基于最近一个波段计算斐波那契回调位"""
    if len(highs) == 0 or len(lows) == 0:
        return []
    last_high_idx, last_high = highs[-1]
    last_low_idx, last_low = lows[-1]
    levels = []

    # 判断最近一个波段方向
    if last_high_idx > last_low_idx:  # 先低后高（上升波段）
        high = last_high
        low = last_low
        fib_50 = high - (high - low) * 0.5
        fib_618 = high - (high - low) * 0.618
        # 在上升波段中，回调位是支撑
        levels.append({'name': '斐波那契0.5', 'type': 'support', 'value': fib_50})
        levels.append({'name': '斐波那契0.618', 'type': 'support', 'value': fib_618})
    else:  # 先高后低（下降波段）
        high = last_high
        low = last_low
        fib_50 = low + (high - low) * 0.5
        fib_618 = low + (high - low) * 0.618
        # 在下降波段中，回调位是阻力
        levels.append({'name': '斐波那契0.5', 'type': 'resistance', 'value': fib_50})
        levels.append({'name': '斐波那契0.618', 'type': 'resistance', 'value': fib_618})
    return levels

def compute_all_levels(df, fetcher):
    """汇总所有支撑阻力位"""
    highs, lows = find_swing_points(df)
    levels = []

    # 趋势线
    uptrend_support, downtrend_resistance = trend_lines(df, highs, lows)
    if uptrend_support:
        levels.append({'name': '上升趋势线', 'type': 'support', 'value': uptrend_support})
    if downtrend_resistance:
        levels.append({'name': '下降趋势线', 'type': 'resistance', 'value': downtrend_resistance})

    # 水平支撑阻力
    levels.extend(horizontal_levels(highs, lows))

    # 突破位
    levels.extend(last_breakout(df, highs, lows))

    # 最近极值
    levels.extend(last_extremes(highs, lows))

    # EMA20（类型依赖整体趋势，后面在提醒时动态判断，这里先标记为通用）
    ema20 = fetcher.ema['15m']['EMA20']
    levels.append({'name': 'EMA20', 'type': 'dynamic', 'value': ema20})

    # 斐波那契
    levels.extend(fibonacci_levels(highs, lows, df))

    return levels

# ==================== 反转形态检测模块 ====================
def is_hammer(candle, trend_dir='down'):
    """
    锤子线/上吊线识别
    简化条件：下影线较长，实体较小，位于上端
    """
    open, high, low, close = candle['open'], candle['high'], candle['low'], candle['close']
    body = abs(close - open)
    lower_shadow = min(open, close) - low
    upper_shadow = high - max(open, close)
    total_range = high - low
    if total_range == 0:
        return False
    # 实体较小，下影线较长，上影线短
    if body < total_range * 0.3 and lower_shadow > body * 2 and upper_shadow < body * 0.5:
        # 根据趋势方向区分锤子线（下跌后）和上吊线（上涨后）
        # 这里不依赖趋势，返回形态名称由调用者判断
        return True
    return False

def is_engulfing(prev, curr):
    """吞没形态：第二根实体完全吞没第一根实体"""
    prev_body = abs(prev['close'] - prev['open'])
    curr_body = abs(curr['close'] - curr['open'])
    if prev_body == 0 or curr_body == 0:
        return False
    # 看涨吞没：prev阴线，curr阳线，且curr实体覆盖prev实体
    if prev['close'] < prev['open'] and curr['close'] > curr['open']:
        if curr['close'] > prev['open'] and curr['open'] < prev['close']:
            return 'bullish_engulfing'
    # 看跌吞没：prev阳线，curr阴线，且curr实体覆盖prev实体
    elif prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr['open'] > prev['close'] and curr['close'] < prev['open']:
            return 'bearish_engulfing'
    return None

def detect_recent_reversal(df, lookback=2):
    """
    检测最近1-2根K线是否有反转形态
    返回 (has_reversal, pattern_name)
    """
    if len(df) < 2:
        return False, None

    last = df.iloc[-1].to_dict()
    prev = df.iloc[-2].to_dict()

    # 单K线形态：锤子线/上吊线
    if is_hammer(last):
        # 需要结合前一根趋势判断，但简单返回名称
        return True, 'single_candle_hammer'

    # 双K线形态：吞没
    engulf = is_engulfing(prev, last)
    if engulf:
        return True, engulf

    # 可继续添加其他形态如刺透、乌云盖顶等

    return False, None

# ==================== 提醒模块（企业微信） ====================
def send_wework_msg(message):
    """发送企业微信机器人消息（纯文本）"""
    url = WEWORK_WEBHOOK_URL
    headers = {'Content-Type': 'application/json'}
    data = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code != 200:
            print(f"企业微信发送失败: {response.text}")
    except Exception as e:
        print(f"企业微信发送异常: {e}")

def get_trend_name(trend):
    """将趋势代码转换为中文名称"""
    trend_map = {
        'bull': '🟢 看涨',
        'bear': '🔴 看跌',
        'neutral': '⚪ 中性'
    }
    return trend_map.get(trend, trend)

def format_price(price, symbol):
    """根据交易对格式化价格显示"""
    # 根据交易对确定小数位数
    if 'JPY' in symbol or 'BTC' in symbol:
        return f"{price:.2f}"
    elif 'EUR' in symbol:
        return f"{price:.5f}"
    else:
        return f"{price:.4f}"

# ==================== 主监控循环 ====================
def main():
    fetcher = DataFetcher(SYMBOL, TIMEFRAMES)
    last_alert = {}  # 记录上次提醒时间，键为位置名称

    print(f"开始监控 {SYMBOL} ...")
    while True:
        try:
            # 更新数据
            fetcher.update_all()
            trend = get_overall_trend(fetcher)
            price = fetcher.get_latest_price()

            # 获取15分钟K线DataFrame
            df_15m = pd.DataFrame(fetcher.data['15m'])

            # 计算所有支撑阻力位
            levels = compute_all_levels(df_15m, fetcher)

            # 检查每个位置
            for level in levels:
                level_name = level['name']
                level_type = level['type']
                level_value = level['value']

                # 处理EMA20动态类型
                if level_name == 'EMA20':
                    # 根据趋势赋予类型
                    if trend == 'bull':
                        level_type = 'support'
                    elif trend == 'bear':
                        level_type = 'resistance'
                    else:
                        level_type = 'both'  # 中性时都提醒

                # 判断是否接近
                if abs(price - level_value) / price <= NEAR_THRESHOLD:
                    # 根据趋势过滤
                    if trend == 'bull' and level_type not in ['support', 'both']:
                        continue
                    if trend == 'bear' and level_type not in ['resistance', 'both']:
                        continue

                    # 冷却时间检查
                    now = time.time()
                    key = f"{level_name}_{level_value}"
                    if key in last_alert and now - last_alert[key] < COOLDOWN_SECONDS:
                        continue

                    # 检测反转形态
                    has_reversal, pattern = detect_recent_reversal(df_15m)
                    reversal_msg = f"，出现{pattern}" if has_reversal else ""

                    # 发送提醒
                    msg = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {SYMBOL}\n"
                           f"趋势: {trend}\n当前价格: {price}\n"
                           f"接近{level_name}: {level_value}{reversal_msg}")
                    send_wework_msg(msg)
                    print(msg)  # 控制台输出
                    last_alert[key] = now

            # 每分钟检查一次
            time.sleep(60)

        except Exception as e:
            print(f"监控异常: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()