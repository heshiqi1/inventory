import pandas as pd
import numpy as np
import time
import requests
import os
import json
import base64
import hashlib
import threading
from datetime import datetime, timedelta
from collections import deque

"""
增加微信企业启动通知，每次支撑阻力机会出现的时候通知机会：品种、价格、趋势方向等等重要信息，支撑多品种同时监控提醒，并且使用twelvedata数据源
"""

# ==================== 配置参数 ====================
# Twelve Data API配置（支持多个 Key，被限流或额度用尽时自动切换）
# 方式1：环境变量 TWELVEDATA_API_KEY，多个用英文逗号分隔，如 "key1,key2,key3"
# 方式2：直接写列表，如 ['key1', 'key2']
_env_keys = os.getenv("TWELVEDATA_API_KEY", "").strip()
if _env_keys:
    TWELVEDATA_API_KEYS = [k.strip() for k in _env_keys.split(",") if k.strip()]
else:
    TWELVEDATA_API_KEYS = ["8ed68089e5114f4893927e4103bb5c6c","61141e293ece4cad906e65413921b012","a8a880312e204b29b800da9cde8f9f9a"]  # 默认单 key，可改为 ['key1','key2']
TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

# 交易对配置（支持多品种）
# 格式：{'内部符号': 'TwelveData符号'}
SYMBOLS = {
    'USDJPY': 'USD/JPY',      # 美元/日元
    'EURUSD': 'EUR/USD',      # 欧元/美元
    # 'BTCUSD': 'BTC/USD',      # 比特币/美元
    'XAUUSD': 'XAU/USD',      # 黄金/美元
    'GBPUSD': 'GBP/USD',      # 英镑/美元
}

# 分析数据的K线周期（twelvedata 格式：1min, 5min, 15min, 30min, 45min, 1h, 2h, 4h, 1day）
ANALYSIS_TIMEFRAME = '15min'
TIMEFRAMES = [ANALYSIS_TIMEFRAME]      # 拉取并用于分析的周期，与上面保持一致即可
# 每次监控触发的时间间隔（秒）：每轮全品种检查完后等待多久再下一轮
MONITOR_INTERVAL_SECONDS = 300

EMA_PERIODS = [20, 50, 100]            # EMA周期
SWING_WINDOW = 2                       # 摆动点识别窗口（左右各几根）
LEVEL_MERGE_THRESHOLD = 0.001          # 水平位合并阈值（0.1%）
NEAR_THRESHOLD = 0.001                 # 价格接近阈值（0.1%）
COOLDOWN_SECONDS = 900                 # 同一位置重复提醒冷却时间（秒）
WEWORK_WEBHOOK_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128'  # 企业微信机器人Webhook地址
WEWORK_SEND_CHART_IMAGE = False         # 通知时是否发送K线图：True=发送，False=仅发文字

# 本地存储配置
MONITOR_DATA_DIR = "monitor_data"       # 监控数据存储目录
MONITOR_CHARTS_DIR = os.path.join(MONITOR_DATA_DIR, "charts")  # K线图保存目录
MONITOR_DATA_FILE = os.path.join(MONITOR_DATA_DIR, "monitor_records.json")  # 监控记录文件
DATA_RETENTION_DAYS = 30               # 数据保留天数（30天）

# API请求频率控制
API_MIN_INTERVAL = 1.0                 # 两次API请求之间的最小间隔（秒）
API_MAX_RETRIES = 5                    # 最大重试次数
API_RETRY_BASE_DELAY = 2.0             # 重试基础延迟（秒）
API_RETRY_MAX_DELAY = 60.0             # 重试最大延迟（秒）

# ==================== API Key 多 Key 切换 ====================
class TwelveDataKeyManager:
    """多 Key 管理：被限流或额度用尽时自动切换到下一个 Key"""
    _lock = threading.Lock()
    _current_index = 0

    @classmethod
    def get_current_key(cls):
        """获取当前使用的 API Key"""
        with cls._lock:
            if not TWELVEDATA_API_KEYS:
                return None
            return TWELVEDATA_API_KEYS[cls._current_index % len(TWELVEDATA_API_KEYS)]

    @classmethod
    def switch_to_next(cls):
        """切换到下一个 Key，返回是否成功切换（有下一个可用）"""
        with cls._lock:
            n = len(TWELVEDATA_API_KEYS)
            if n <= 1:
                return False
            cls._current_index = (cls._current_index + 1) % n
            return True

    @classmethod
    def current_index(cls):
        with cls._lock:
            return cls._current_index % len(TWELVEDATA_API_KEYS) if TWELVEDATA_API_KEYS else 0

    @classmethod
    def key_count(cls):
        return len(TWELVEDATA_API_KEYS)


# ==================== API请求节流控制 ====================
class APIThrottle:
    """API请求节流器，确保请求间隔，避免频繁调用"""
    _lock = threading.Lock()
    _last_request_time = 0.0
    
    @classmethod
    def wait_if_needed(cls, min_interval=API_MIN_INTERVAL):
        """如果需要，等待以确保请求间隔"""
        with cls._lock:
            current_time = time.time()
            elapsed = current_time - cls._last_request_time
            if elapsed < min_interval:
                wait_time = min_interval - elapsed
                time.sleep(wait_time)
            cls._last_request_time = time.time()

# ==================== 数据获取模块 ====================
class DataFetcher:
    def __init__(self, symbol, td_symbol, timeframes, limit=200):
        self.symbol = symbol  # 内部符号
        self.td_symbol = td_symbol  # TwelveData符号
        self.timeframes = timeframes
        self.limit = limit
        self.data = {tf: deque(maxlen=limit) for tf in timeframes}  # 存储K线数据
        self.ema = {tf: {} for tf in timeframes}                    # 存储最新EMA值
        self.last_fetch_time = {tf: 0 for tf in timeframes}          # 记录每个周期的上次获取时间

    def fetch_klines(self, timeframe, force_refresh=False):
        """
        从TwelveData获取指定周期K线数据，返回DataFrame
        force_refresh: 是否强制刷新（忽略时间间隔检查）
        """
        # 检查是否需要刷新（避免频繁请求）
        current_time = time.time()
        if not force_refresh:
            last_fetch = self.last_fetch_time.get(timeframe, 0)
            # 根据周期设置最小刷新间隔
            min_interval_map = {
                '5min': 60,      # 5分钟周期：至少60秒刷新一次
                '15min': 180,    # 15分钟周期：至少180秒刷新一次
                '1h': 600,       # 1小时周期：至少600秒刷新一次
            }
            min_interval = min_interval_map.get(timeframe, 300)  # 默认5分钟
            if current_time - last_fetch < min_interval:
                # 数据还新鲜，不需要刷新
                return None
        
        # API请求节流控制
        APIThrottle.wait_if_needed(API_MIN_INTERVAL)

        # 指数退避重试 + 多 Key 切换（限流/额度用尽时换 Key 再试）
        for attempt in range(API_MAX_RETRIES):
            apikey = TwelveDataKeyManager.get_current_key()
            if not apikey:
                print(f"  [TwelveData错误] 无可用 API Key")
                return None
            params = {
                'symbol': self.td_symbol,
                'interval': timeframe,
                'outputsize': self.limit,
                'apikey': apikey,
                'format': 'JSON',
                'order': 'ASC',  # 升序，最新在末尾
            }
            try:
                resp = requests.get(TWELVEDATA_URL, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                
                # 检查API错误
                if data.get('status') == 'error':
                    code = data.get('code', '')
                    msg = data.get('message', '')
                    
                    # API额度用尽：尝试切换下一个 Key
                    if 'run out of API credits' in msg or 'API credits' in msg:
                        print(f"  [TwelveData] {self.symbol} {timeframe}: 当前 Key 额度已用完 (Key#{TwelveDataKeyManager.current_index() + 1})")
                        if TwelveDataKeyManager.switch_to_next():
                            print(f"  [TwelveData] 已切换到下一个 Key (共 {TwelveDataKeyManager.key_count()} 个)，重试...")
                            continue
                        print(f"  ⚠️ 所有 API Key 额度已用完，请等待明天或添加更多 Key")
                        return None
                    
                    # 429 限流：先尝试切换 Key，再退避重试
                    if str(code) == '429':
                        if TwelveDataKeyManager.switch_to_next():
                            print(f"  [限流] {self.symbol} {timeframe}: 已切换到下一 Key，重试...")
                            continue
                        if attempt < API_MAX_RETRIES - 1:
                            delay = min(API_RETRY_BASE_DELAY * (2 ** attempt), API_RETRY_MAX_DELAY)
                            print(f"  [限流] {self.symbol} {timeframe}: 等待 {delay:.1f}秒后重试 (尝试 {attempt+1}/{API_MAX_RETRIES})")
                            time.sleep(delay)
                            continue
                        print(f"  [TwelveData错误] {self.symbol} {timeframe}: 达到最大重试次数，限流错误")
                        return None
                    
                    # 其他错误
                    print(f"  [TwelveData错误] {self.symbol} {timeframe}: code={code} {msg}")
                    if attempt < API_MAX_RETRIES - 1:
                        delay = min(API_RETRY_BASE_DELAY * (2 ** attempt), API_RETRY_MAX_DELAY)
                        time.sleep(delay)
                        continue
                    return None
                
                # 成功获取数据
                values = data.get('values')
                if not values:
                    print(f"  [TwelveData] {self.symbol} {timeframe} 无数据返回")
                    return None
                
                # 更新最后获取时间
                self.last_fetch_time[timeframe] = time.time()
                break
                
            except requests.Timeout as e:
                print(f"  [TwelveData超时 attempt {attempt+1}/{API_MAX_RETRIES}] {self.symbol} {timeframe}: {e}")
                if attempt < API_MAX_RETRIES - 1:
                    delay = min(API_RETRY_BASE_DELAY * (2 ** attempt), API_RETRY_MAX_DELAY)
                    time.sleep(delay)
                    continue
                return None
                
            except requests.RequestException as e:
                print(f"  [TwelveData请求失败 attempt {attempt+1}/{API_MAX_RETRIES}] {self.symbol} {timeframe}: {e}")
                if attempt < API_MAX_RETRIES - 1:
                    delay = min(API_RETRY_BASE_DELAY * (2 ** attempt), API_RETRY_MAX_DELAY)
                    time.sleep(delay)
                    continue
                return None
                
            except Exception as e:
                print(f"  [TwelveData未知错误] {self.symbol} {timeframe}: {e}")
                return None
        
        # 解析为DataFrame
        df = pd.DataFrame(values)
        df = df.rename(columns={
            'datetime': 'timestamp',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
        })
        
        # 设置时间索引
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        df = df[~df.index.duplicated(keep='first')]
        
        # 确保数值类型
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        
        # 重置索引为列
        df = df.reset_index()
        
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

    def update_all(self, force_refresh=False):
        """
        更新所有周期数据，并计算EMA
        force_refresh: 是否强制刷新所有周期数据
        """
        for tf in self.timeframes:
            df = self.fetch_klines(tf, force_refresh=force_refresh)
            if df is None:
                # fetch_klines返回None可能是数据还新鲜，不需要刷新
                # 如果数据为空，继续使用旧数据
                continue
            if len(df) == 0:
                continue
            # 检测是否有新K线（通过最后一条时间戳）
            if len(self.data[tf]) == 0 or df['timestamp'].iloc[-1] != self.data[tf][-1]['timestamp']:
                self.data[tf] = df.to_dict('records')
                self.ema[tf] = self.calc_ema(df, EMA_PERIODS)

    def get_latest_price(self):
        """获取当前最新成交价（使用分析周期最新K线的收盘价）"""
        if len(self.data.get(ANALYSIS_TIMEFRAME, [])) > 0:
            return self.data[ANALYSIS_TIMEFRAME][-1]['close']
        for tf in self.timeframes:
            if len(self.data[tf]) > 0:
                return self.data[tf][-1]['close']
        return None

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
    """基于分析周期数据判断趋势，返回 'bull'/'bear'/'neutral'"""
    trend = check_trend(fetcher.ema.get(ANALYSIS_TIMEFRAME, {}))
    return trend if trend else 'neutral'

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

def get_trend_line_segments(df, highs, lows):
    """
    获取趋势线的两点坐标，用于绘图。
    返回 (support_segment, resistance_segment)，每个为 ((idx0, price0), (idx1, price1)) 或 None。
    """
    support_seg = None
    resistance_seg = None
    current_idx = len(df) - 1

    if len(lows) >= 2:
        l1_idx, l1_price = lows[-1]
        for i in range(len(lows) - 2, -1, -1):
            l0_idx, l0_price = lows[i]
            if l0_price < l1_price:
                k = (l1_price - l0_price) / (l1_idx - l0_idx)
                support_val = l0_price + k * (current_idx - l0_idx)
                support_seg = ((l0_idx, l0_price), (current_idx, support_val))
                break

    if len(highs) >= 2:
        h1_idx, h1_price = highs[-1]
        for i in range(len(highs) - 2, -1, -1):
            h0_idx, h0_price = highs[i]
            if h0_price > h1_price:
                k = (h1_price - h0_price) / (h1_idx - h0_idx)
                resistance_val = h0_price + k * (current_idx - h0_idx)
                resistance_seg = ((h0_idx, h0_price), (current_idx, resistance_val))
                break

    return support_seg, resistance_seg


def get_channel_line_segments(df, support_seg, resistance_seg):
    """
    根据趋势线计算通道线（与趋势线平行，过波段极值点）。
    返回 (upper_channel_seg, lower_channel_seg)，用于上升通道上轨、下降通道下轨；无则为 None。
    """
    upper_seg = None
    lower_seg = None
    current_idx = len(df) - 1
    if current_idx < 0:
        return upper_seg, lower_seg

    # 上升通道上轨：与支撑线平行，过 [l0_idx, current_idx] 内最高点
    if support_seg:
        (i0, p0), (i1, p1) = support_seg
        if i1 != i0:
            k = (p1 - p0) / (i1 - i0)
            start_i, end_i = max(0, int(i0)), min(current_idx, len(df) - 1)
            slice_high = df['high'].iloc[start_i:end_i + 1]
            if len(slice_high) > 0:
                max_high_idx = start_i + int(slice_high.values.argmax())
                max_high = float(df['high'].iloc[max_high_idx])
                p_at_0 = max_high + k * (i0 - max_high_idx)
                p_at_1 = max_high + k * (i1 - max_high_idx)
                upper_seg = ((i0, p_at_0), (i1, p_at_1))

    # 下降通道下轨：与阻力线平行，过 [h0_idx, current_idx] 内最低点
    if resistance_seg:
        (i0, p0), (i1, p1) = resistance_seg
        if i1 != i0:
            k = (p1 - p0) / (i1 - i0)
            start_i, end_i = max(0, int(i0)), min(current_idx, len(df) - 1)
            slice_low = df['low'].iloc[start_i:end_i + 1]
            if len(slice_low) > 0:
                min_low_idx = start_i + int(slice_low.values.argmin())
                min_low = float(df['low'].iloc[min_low_idx])
                p_at_0 = min_low + k * (i0 - min_low_idx)
                p_at_1 = min_low + k * (i1 - min_low_idx)
                lower_seg = ((i0, p_at_0), (i1, p_at_1))

    return upper_seg, lower_seg


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
    ema20 = fetcher.ema.get(ANALYSIS_TIMEFRAME, {}).get('EMA20')
    if ema20:
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

# ==================== 本地存储模块 ====================
def ensure_data_dir():
    """确保数据目录存在"""
    if not os.path.exists(MONITOR_DATA_DIR):
        os.makedirs(MONITOR_DATA_DIR)
        print(f"[存储] 创建数据目录: {MONITOR_DATA_DIR}")

def load_monitor_records():
    """加载历史监控记录"""
    ensure_data_dir()
    if not os.path.exists(MONITOR_DATA_FILE):
        return []
    
    try:
        with open(MONITOR_DATA_FILE, 'r', encoding='utf-8') as f:
            records = json.load(f)
        return records if isinstance(records, list) else []
    except Exception as e:
        print(f"[存储] 加载历史记录失败: {e}")
        return []

def save_monitor_record(symbol, price, trend, near_levels, reversal_info=None):
    """保存单次监控记录"""
    ensure_data_dir()
    
    record = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'timestamp_unix': time.time(),
        'symbol': symbol,
        'price': price,
        'trend': trend,
        'near_levels': [
            {
                'name': level['name'],
                'type': level['type'],
                'value': level['value']
            }
            for level in near_levels
        ],
        'reversal_pattern': reversal_info[1] if reversal_info and reversal_info[0] else None,
        'has_opportunity': len(near_levels) > 0
    }
    
    # 加载现有记录
    records = load_monitor_records()
    
    # 添加新记录
    records.append(record)
    
    # 清理旧数据（保留最近N天）
    cutoff_time = time.time() - (DATA_RETENTION_DAYS * 24 * 3600)
    records = [r for r in records if r.get('timestamp_unix', 0) > cutoff_time]
    
    # 保存到文件
    try:
        with open(MONITOR_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[存储] 保存监控记录失败: {e}")
        return False

def save_monitor_summary(symbol, price, trend, all_levels, near_levels):
    """保存监控摘要（即使没有接近的位置也保存）"""
    ensure_data_dir()
    
    # 计算所有支撑阻力位统计
    support_levels = [l for l in all_levels if l.get('type') == 'support']
    resistance_levels = [l for l in all_levels if l.get('type') == 'resistance']
    
    summary = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'timestamp_unix': time.time(),
        'symbol': symbol,
        'price': price,
        'trend': trend,
        'statistics': {
            'total_levels': len(all_levels),
            'support_levels_count': len(support_levels),
            'resistance_levels_count': len(resistance_levels),
            'near_levels_count': len(near_levels),
            'has_opportunity': len(near_levels) > 0
        },
        'near_levels': [
            {
                'name': level['name'],
                'type': level['type'],
                'value': level['value'],
                'distance_pct': abs(price - level['value']) / price * 100 if price else 0
            }
            for level in near_levels
        ]
    }
    
    # 保存到单独的文件（按品种分类）
    symbol_file = os.path.join(MONITOR_DATA_DIR, f"{symbol}_summary.json")
    
    try:
        # 加载该品种的历史摘要
        if os.path.exists(symbol_file):
            with open(symbol_file, 'r', encoding='utf-8') as f:
                summaries = json.load(f)
        else:
            summaries = []
        
        summaries.append(summary)
        
        # 清理旧数据
        cutoff_time = time.time() - (DATA_RETENTION_DAYS * 24 * 3600)
        summaries = [s for s in summaries if s.get('timestamp_unix', 0) > cutoff_time]
        
        # 保存
        with open(symbol_file, 'w', encoding='utf-8') as f:
            json.dump(summaries, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"[存储] 保存监控摘要失败: {e}")
        return False


# ==================== 通知前K线图绘制模块 ====================
def draw_opportunity_chart(symbol, df_15m, price, all_levels, near_levels, highs, lows, trend):
    """
    在通知提醒前本地绘制K线图：含趋势线、水平支撑/阻力、斐波那契位、当前价与关键价位标注。
    返回保存的图片路径，失败返回 None。
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.patches as mpatches

        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        ensure_data_dir()
        os.makedirs(MONITOR_CHARTS_DIR, exist_ok=True)

        # 准备绘图用 DataFrame：索引为时间
        if 'timestamp' in df_15m.columns:
            plot_df = df_15m.set_index('timestamp').copy()
        else:
            plot_df = df_15m.copy()
        plot_df.index = pd.to_datetime(plot_df.index)
        # 列名统一为小写
        for c in ['open', 'high', 'low', 'close']:
            if c not in plot_df.columns and c.capitalize() in plot_df.columns:
                plot_df[c] = plot_df[c.capitalize()]
        if 'close' not in plot_df.columns:
            return None

        # 计算 EMA 用于图中显示
        for p in EMA_PERIODS:
            plot_df[f'ema_{p}'] = plot_df['close'].ewm(span=p, adjust=False).mean()

        # 最近约 120 根 K 线
        show_bars = min(120, len(plot_df))
        chart_df = plot_df.iloc[-show_bars:].copy()

        fig, ax = plt.subplots(figsize=(14, 7))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='#e0e0e0')
        for spine in ax.spines.values():
            spine.set_color('#444466')

        # ----- K 线（蜡烛图） -----
        for i in range(len(chart_df)):
            dt = chart_df.index[i]
            op = float(chart_df['open'].iloc[i])
            hi = float(chart_df['high'].iloc[i])
            lo = float(chart_df['low'].iloc[i])
            cl = float(chart_df['close'].iloc[i])
            is_bull = cl >= op
            color = '#26a69a' if is_bull else '#ef5350'
            ax.plot([dt, dt], [lo, hi], color=color, linewidth=0.8, zorder=2)
            body_lo, body_hi = min(op, cl), max(op, cl)
            body_h = max(body_hi - body_lo, (hi - lo) * 0.002)
            rect = mpatches.Rectangle(
                (mdates.date2num(dt) - 0.0015, body_lo), 0.003, body_h,
                facecolor=color, edgecolor=color, alpha=0.9, zorder=3
            )
            ax.add_patch(rect)

        # ----- EMA -----
        ax.plot(chart_df.index, chart_df['ema_20'], color='#42a5f5', linewidth=1.0, label='EMA20', zorder=4)
        ax.plot(chart_df.index, chart_df['ema_50'], color='#ffa726', linewidth=1.0, label='EMA50', zorder=4)
        ax.plot(chart_df.index, chart_df['ema_100'], color='#9467bd', linewidth=1.0, label='EMA100', zorder=4)

        # ----- 趋势线（线段） -----
        support_seg, resistance_seg = get_trend_line_segments(plot_df.reset_index(drop=True), highs, lows)
        # 绘图时使用 bar 索引对应到 chart_df 的时间：plot_df 与 chart_df 的对应关系是末尾对齐
        base_idx = len(plot_df) - len(chart_df)
        def idx_to_time(bar_idx):
            if bar_idx < base_idx:
                bar_idx = base_idx
            if bar_idx >= len(plot_df):
                bar_idx = len(plot_df) - 1
            return plot_df.index[bar_idx]

        if support_seg:
            (i0, p0), (i1, p1) = support_seg
            t0, t1 = idx_to_time(i0), idx_to_time(i1)
            ax.plot([t0, t1], [p0, p1], color='#26a69a', linestyle='-', linewidth=1.5, label='上升趋势线(支撑)', zorder=5)
        if resistance_seg:
            (i0, p0), (i1, p1) = resistance_seg
            t0, t1 = idx_to_time(i0), idx_to_time(i1)
            ax.plot([t0, t1], [p0, p1], color='#ef5350', linestyle='-', linewidth=1.5, label='下降趋势线(阻力)', zorder=5)

        # ----- 通道线（与趋势线平行，过波段极值） -----
        df_for_channel = plot_df.reset_index(drop=True)
        upper_channel_seg, lower_channel_seg = get_channel_line_segments(df_for_channel, support_seg, resistance_seg)
        if upper_channel_seg:
            (i0, p0), (i1, p1) = upper_channel_seg
            t0, t1 = idx_to_time(i0), idx_to_time(i1)
            ax.plot([t0, t1], [p0, p1], color='#81c784', linestyle='--', linewidth=1.2, alpha=0.9, label='上升通道上轨', zorder=5)
        if lower_channel_seg:
            (i0, p0), (i1, p1) = lower_channel_seg
            t0, t1 = idx_to_time(i0), idx_to_time(i1)
            ax.plot([t0, t1], [p0, p1], color='#e57373', linestyle='--', linewidth=1.2, alpha=0.9, label='下降通道下轨', zorder=5)

        # ----- 最近一个波段最高值、最低值（上涨/下降趋势均绘制） -----
        wave_high = highs[-1][1] if highs else None
        wave_low = lows[-1][1] if lows else None
        if wave_high is not None:
            ax.axhline(wave_high, color='#ffb74d', linestyle='-.', linewidth=1.2, alpha=0.9, label=f'波段高点 {format_price(wave_high, symbol)}', zorder=5)
        if wave_low is not None:
            ax.axhline(wave_low, color='#64b5f6', linestyle='-.', linewidth=1.2, alpha=0.9, label=f'波段低点 {format_price(wave_low, symbol)}', zorder=5)

        # ----- 斐波那契 50%、0.618 回调位（明确绘制并标注） -----
        fib_50 = next((lev['value'] for lev in all_levels if lev.get('name') and '0.5' in lev.get('name', '') and '斐波' in lev.get('name', '')), None)
        fib_618 = next((lev['value'] for lev in all_levels if '0.618' in lev.get('name', '')), None)
        if fib_50 is not None:
            ax.axhline(fib_50, color='#ab47bc', linestyle=':', linewidth=1.2, alpha=0.9, zorder=5)
        if fib_618 is not None:
            ax.axhline(fib_618, color='#7e57c2', linestyle=':', linewidth=1.2, alpha=0.9, zorder=5)

        # ----- 水平支撑/阻力、斐波那契位 -----
        for level in all_levels:
            val = level.get('value')
            if val is None:
                continue
            name = level.get('name', '')
            ltype = level.get('type', '')
            if '趋势线' in name:
                continue  # 已在上面画过
            if '斐波' in name or '0.5' in name or '0.618' in name:
                ax.axhline(val, color='#ab47bc', linestyle=':', linewidth=1.0, alpha=0.85, zorder=5)
            elif ltype == 'support':
                ax.axhline(val, color='#26a69a', linestyle='--', linewidth=1.0, alpha=0.85, zorder=5)
            elif ltype == 'resistance':
                ax.axhline(val, color='#ef5350', linestyle='--', linewidth=1.0, alpha=0.85, zorder=5)
            elif ltype == 'dynamic':
                ax.axhline(val, color='#42a5f5', linestyle='-.', linewidth=1.0, alpha=0.8, zorder=5)

        # ----- 当前价格线 -----
        ax.axhline(price, color='#ffeb3b', linestyle='-', linewidth=2.0, alpha=0.95, label=f'当前价 {format_price(price, symbol)}', zorder=6)

        # ----- 图内标注：当前价与关键位置价格 -----
        last_time = chart_df.index[-1]
        ax.scatter([last_time], [price], color='#ffeb3b', s=80, zorder=7, edgecolors='white', linewidths=1.5)
        ax.annotate(
            f'当前价 {format_price(price, symbol)}',
            xy=(last_time, price), xytext=(12, 0), textcoords='offset points',
            color='#ffeb3b', fontsize=9, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e', alpha=0.9),
            zorder=8
        )
        # 标注接近的关键位置
        for level in near_levels:
            val = level.get('value')
            if val is None:
                continue
            ax.axhline(val, color='#ff9800', linestyle='-', linewidth=0.8, alpha=0.5, zorder=5)
            ax.scatter([last_time], [val], color='#ff9800', s=50, zorder=7)
            ax.annotate(
                f"{level.get('name', '')} {format_price(val, symbol)}",
                xy=(last_time, val), xytext=(12, 0), textcoords='offset points',
                color='#ff9800', fontsize=8,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a2e', alpha=0.8),
                zorder=8
            )
        # 斐波那契 50%、61.8% 图内标注
        if fib_50 is not None:
            ax.scatter([last_time], [fib_50], color='#ab47bc', s=40, zorder=7)
            ax.annotate(
                f'Fib 50% {format_price(fib_50, symbol)}',
                xy=(last_time, fib_50), xytext=(12, 0), textcoords='offset points',
                color='#ab47bc', fontsize=8,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a2e', alpha=0.8),
                zorder=8
            )
        if fib_618 is not None:
            ax.scatter([last_time], [fib_618], color='#7e57c2', s=40, zorder=7)
            ax.annotate(
                f'Fib 61.8% {format_price(fib_618, symbol)}',
                xy=(last_time, fib_618), xytext=(12, 0), textcoords='offset points',
                color='#7e57c2', fontsize=8,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a2e', alpha=0.8),
                zorder=8
            )

        trend_cn = {'bull': '看涨', 'bear': '看跌', 'neutral': '中性'}.get(trend, trend)
        tf_label = _timeframe_display_name(ANALYSIS_TIMEFRAME)
        ax.set_title(
            f'{symbol}  {tf_label}K线  趋势: {trend_cn}  价格: {format_price(price, symbol)}  '
            f'时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            color='#e0e0e0', fontsize=11, fontweight='bold', pad=10
        )
        ax.set_ylabel('价格', color='#e0e0e0', fontsize=10)
        ax.legend(loc='upper left', fontsize=8, facecolor='#1a1a2e', labelcolor='#e0e0e0', framealpha=0.8)
        ax.grid(True, alpha=0.15, color='#444466')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        plt.xticks(rotation=25)
        plt.tight_layout(rect=[0, 0, 1, 0.96])

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{symbol}_{ts}.png"
        filepath = os.path.join(MONITOR_CHARTS_DIR, filename)
        plt.savefig(filepath, dpi=100, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close(fig)
        print(f"  [图表] 已保存: {filepath}")
        return filepath
    except Exception as e:
        print(f"  [图表保存失败] {e}")
        import traceback
        traceback.print_exc()
        return None


# ==================== 提醒模块（企业微信） ====================
def send_wework_msg(message, msgtype='text'):
    """发送企业微信机器人消息"""
    url = WEWORK_WEBHOOK_URL
    headers = {'Content-Type': 'application/json'}
    
    if msgtype == 'markdown':
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": message
            }
        }
    else:
        data = {
            "msgtype": "text",
            "text": {
                "content": message
            }
        }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code != 200:
            print(f"企业微信发送失败: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"企业微信发送异常: {e}")
        return False


def send_wework_image(image_path, max_size_mb=2):
    """
    发送企业微信机器人图片消息。图片需为 JPG/PNG，不超过 2MB。
    若文件超过 max_size_mb，先尝试缩小后发送（此处仅校验大小，超限则跳过发送并提示）。
    """
    if not image_path or not os.path.isfile(image_path):
        return False
    try:
        with open(image_path, 'rb') as f:
            raw = f.read()
        size_mb = len(raw) / (1024 * 1024)
        if size_mb > max_size_mb:
            print(f"  [企业微信] 图片过大 ({size_mb:.2f}MB > {max_size_mb}MB)，跳过发送")
            return False
        base64_content = base64.b64encode(raw).decode('ascii')
        md5_content = hashlib.md5(raw).hexdigest()
        url = WEWORK_WEBHOOK_URL
        headers = {'Content-Type': 'application/json'}
        data = {
            "msgtype": "image",
            "image": {
                "base64": base64_content,
                "md5": md5_content
            }
        }
        response = requests.post(url, headers=headers, json=data, timeout=15)
        if response.status_code != 200:
            print(f"企业微信图片发送失败: {response.text}")
            return False
        print(f"  [企业微信] 已发送K线图")
        return True
    except Exception as e:
        print(f"企业微信发送图片异常: {e}")
        return False


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
    if price is None:
        return "N/A"
    # 根据交易对确定小数位数
    if 'JPY' in symbol:
        return f"{price:.2f}"
    elif 'XAU' in symbol or 'XAG' in symbol:  # 黄金、白银
        return f"{price:.2f}"
    elif 'BTC' in symbol:
        return f"{price:.2f}"
    elif 'EUR' in symbol or 'GBP' in symbol or 'AUD' in symbol:
        return f"{price:.5f}"
    else:
        return f"{price:.4f}"

def format_opportunity_msg_summary(symbol, price, trend, near_levels, reversal_info=None):
    """格式化支撑阻力机会汇总通知消息。关键位置+反转形态时使用重点通知格式。"""
    trend_emoji = {'bull': '🟢', 'bear': '🔴'}
    trend_name = {'bull': '看涨', 'bear': '看跌'}
    pattern_cn = {
        'single_candle_hammer': '锤子线',
        'bullish_engulfing': '看涨吞没',
        'bearish_engulfing': '看跌吞没'
    }
    emoji = trend_emoji.get(trend, '')
    trend_text = trend_name.get(trend, '')
    support_levels = [l for l in near_levels if l['type'] == 'support']
    resistance_levels = [l for l in near_levels if l['type'] == 'resistance']
    has_reversal_at_key = bool(near_levels and reversal_info and reversal_info[0])

    if has_reversal_at_key:
        # 重点通知：关键位置附近出现反转形态
        pattern_name = reversal_info[1]
        pattern_text = pattern_cn.get(pattern_name, pattern_name)
        msg = f"""**🚨 重点通知：关键位置附近出现反转形态**

**⚠️ 此信号需重点关注**：价格接近支撑/阻力关键位，且出现 **{pattern_text}** 反转形态，潜在转折概率较高。

**品种**: {symbol}
**当前价格**: {format_price(price, symbol)}
**趋势方向**: {emoji} {trend_text}
**反转形态**: {pattern_text}
**分析周期**: {_timeframe_display_name(ANALYSIS_TIMEFRAME)}

"""
    else:
        msg = f"""**📊 支撑阻力机会通知**

**品种**: {symbol}
**当前价格**: {format_price(price, symbol)}
**趋势方向**: {emoji} {trend_text}
**分析周期**: {_timeframe_display_name(ANALYSIS_TIMEFRAME)}

"""

    # 支撑位
    if support_levels:
        msg += "**🔵 接近支撑位:**\n"
        for level in support_levels:
            distance_pct = abs(price - level['value']) / price * 100 if price else 0
            msg += f"  • {level['name']}: {format_price(level['value'], symbol)} (距离: {distance_pct:.3f}%)\n"
        msg += "\n"
    # 阻力位
    if resistance_levels:
        msg += "**🔴 接近阻力位:**\n"
        for level in resistance_levels:
            distance_pct = abs(price - level['value']) / price * 100 if price else 0
            msg += f"  • {level['name']}: {format_price(level['value'], symbol)} (距离: {distance_pct:.3f}%)\n"
        msg += "\n"
    # 非重点通知时仍单独标出反转形态（若存在）
    if reversal_info and reversal_info[0] and not has_reversal_at_key:
        pattern_name = reversal_info[1]
        msg += f"**⚠️ 反转形态**: {pattern_cn.get(pattern_name, pattern_name)}\n\n"
    msg += f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return msg

# ==================== 主监控循环 ====================
def _timeframe_display_name(tf):
    """将 twelvedata 周期格式转为中文显示"""
    _names = {'1min': '1分钟', '5min': '5分钟', '15min': '15分钟', '30min': '30分钟',
              '45min': '45分钟', '1h': '1小时', '2h': '2小时', '4h': '4小时', '1day': '1日'}
    return _names.get(tf, tf)


def send_startup_notification():
    """发送启动通知"""
    symbol_list = ', '.join(SYMBOLS.keys())
    tf_display = _timeframe_display_name(ANALYSIS_TIMEFRAME)
    msg = f"""**🚀 支撑阻力监控系统已启动**

**监控品种**: {symbol_list}
**分析周期**: {tf_display}（{ANALYSIS_TIMEFRAME}）
**监控间隔**: {MONITOR_INTERVAL_SECONDS} 秒/轮
**通知条件**: 仅在看涨/看跌趋势时通知，中性趋势不通知
**启动时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

系统将按设定间隔监控支撑阻力机会并推送汇总通知。"""
    send_wework_msg(msg, msgtype='markdown')
    print(f"[启动通知] 已发送，监控品种: {symbol_list}，分析周期: {tf_display}，监控间隔: {MONITOR_INTERVAL_SECONDS}秒")

def monitor_symbol(symbol, td_symbol, fetchers, last_alerts):
    """监控单个品种，汇总所有接近的支撑阻力位一次性通知"""
    fetcher = fetchers[symbol]
    
    try:
        # 更新数据
        fetcher.update_all()
        trend = get_overall_trend(fetcher)
        price = fetcher.get_latest_price()
        
        if price is None:
            print(f"[{symbol}] 无法获取价格数据，跳过本次检查")
            return

        # 获取分析周期K线 DataFrame
        if len(fetcher.data.get(ANALYSIS_TIMEFRAME, [])) == 0:
            print(f"[{symbol}] {ANALYSIS_TIMEFRAME} 数据为空，跳过本次检查")
            return
        df_15m = pd.DataFrame(fetcher.data[ANALYSIS_TIMEFRAME])

        # 计算所有支撑阻力位
        levels = compute_all_levels(df_15m, fetcher)

        # 收集所有接近的位置
        near_levels = []
        now = time.time()
        
        # 中性趋势不通知，但仍需要保存数据
        should_notify = (trend != 'neutral')
        
        for level in levels:
            level_name = level['name']
            level_type = level['type']
            level_value = level['value']
            
            if level_value is None:
                continue

            # 处理EMA20动态类型
            if level_name == 'EMA20':
                # 根据趋势赋予类型
                if trend == 'bull':
                    level_type = 'support'
                elif trend == 'bear':
                    level_type = 'resistance'
                else:
                    continue  # 中性时不处理EMA20

            # 判断是否接近
            if abs(price - level_value) / price <= NEAR_THRESHOLD:
                # 根据趋势过滤（仅在看涨/看跌时通知）
                if trend == 'bull' and level_type not in ['support']:
                    continue
                if trend == 'bear' and level_type not in ['resistance']:
                    continue

                # 冷却时间检查（仅在有通知权限时检查）
                if should_notify:
                    key = f"{symbol}_{level_name}_{level_value:.6f}"
                    if key in last_alerts and now - last_alerts[key] < COOLDOWN_SECONDS:
                        continue

                # 添加到接近列表（即使中性趋势也记录，但不通知）
                near_levels.append({
                    'name': level_name,
                    'type': level_type,
                    'value': level_value,
                    'key': f"{symbol}_{level_name}_{level_value:.6f}" if should_notify else None
                })

        # 检测反转形态
        has_reversal, pattern = detect_recent_reversal(df_15m)
        reversal_info = (has_reversal, pattern) if has_reversal else None
        
        # 保存监控数据到本地（无论是否有接近的位置、无论趋势如何都保存）
        save_monitor_summary(symbol, price, trend, levels, near_levels)
        
        # 如果有接近的位置且趋势不是中性，先本地绘图再发送通知并保存详细记录
        if near_levels and should_notify:
            # 先绘制K线图（趋势线、通道线、波段高低点、斐波50%/61.8%、当前价与关键价位）
            highs, lows = find_swing_points(df_15m)
            chart_path = draw_opportunity_chart(symbol, df_15m, price, levels, near_levels, highs, lows, trend)
            # 格式化并发送汇总提醒
            msg = format_opportunity_msg_summary(symbol, price, trend, near_levels, reversal_info)
            send_wework_msg(msg, msgtype='markdown')
            # 通知时是否发送K线图（由 WEWORK_SEND_CHART_IMAGE 控制）
            if WEWORK_SEND_CHART_IMAGE and chart_path:
                send_wework_image(chart_path)
            
            # 保存详细监控记录（有接近位置的情况）
            save_monitor_record(symbol, price, trend, near_levels, reversal_info)
            
            # 控制台输出（关键位置+反转形态时标为重点）
            level_names = [l['name'] for l in near_levels]
            focus_tag = "【重点】关键位+反转形态 | " if (near_levels and has_reversal) else ""
            console_msg = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {focus_tag}{symbol} | "
                          f"趋势: {get_trend_name(trend)} | 价格: {format_price(price, symbol)} | "
                          f"接近位置: {', '.join(level_names)}")
            if has_reversal:
                console_msg += f" | 反转形态: {pattern}"
            print(console_msg)
            
            # 更新所有位置的提醒时间
            for level in near_levels:
                last_alerts[level['key']] = now
        elif near_levels and not should_notify:
            # 中性趋势有接近位置，但不通知（已保存数据）
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | "
                  f"趋势: {get_trend_name(trend)} | 价格: {format_price(price, symbol)} | "
                  f"有接近位置但趋势中性（不通知，已保存监控数据）")
        else:
            # 没有接近的位置，静默保存
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | "
                  f"趋势: {get_trend_name(trend)} | 价格: {format_price(price, symbol)} | "
                  f"无接近位置（已保存监控数据）")

    except Exception as e:
        print(f"[{symbol}] 监控异常: {e}")
        import traceback
        traceback.print_exc()

def main():
    # 初始化数据目录
    ensure_data_dir()
    print(f"[存储] 监控数据将保存到: {MONITOR_DATA_DIR}")
    print(f"[存储] 数据保留天数: {DATA_RETENTION_DAYS}天")
    print(f"[TwelveData] 已加载 {TwelveDataKeyManager.key_count()} 个 API Key（限流/额度用尽时自动切换）")
    
    # 初始化所有品种的数据获取器
    fetchers = {}
    for symbol, td_symbol in SYMBOLS.items():
        fetchers[symbol] = DataFetcher(symbol, td_symbol, TIMEFRAMES)
        print(f"[初始化] {symbol} -> {td_symbol}")
    
    # 每个品种的提醒记录
    last_alerts = {}  # 格式: {symbol_level_name_value: timestamp}
    
    # 发送启动通知
    send_startup_notification()
    
    print(f"\n开始监控 {len(SYMBOLS)} 个品种 | 分析周期: {_timeframe_display_name(ANALYSIS_TIMEFRAME)} | 每轮间隔: {MONITOR_INTERVAL_SECONDS} 秒")
    print("=" * 60)
    
    # 首次数据加载（强制刷新）
    print("正在加载初始数据...")
    success_count = 0
    for symbol in SYMBOLS.keys():
        try:
            # 首次加载强制刷新
            fetchers[symbol].update_all(force_refresh=True)
            # 品种间稍作延迟，避免API限流
            time.sleep(API_MIN_INTERVAL)
            
            price = fetchers[symbol].get_latest_price()
            if price is None:
                print(f"[{symbol}] ⚠️ 数据加载失败：无法获取价格数据（可能是API额度用完）")
                continue
            trend = get_overall_trend(fetchers[symbol])
            print(f"[{symbol}] ✓ 数据加载完成 | 价格: {format_price(price, symbol)} | 趋势: {get_trend_name(trend)}")
            success_count += 1
        except Exception as e:
            print(f"[{symbol}] ✗ 初始数据加载失败: {e}")
            # 即使失败也等待一下，避免连续失败导致限流
            time.sleep(API_MIN_INTERVAL)
    
    if success_count == 0:
        print("\n⚠️ 警告：所有品种数据加载失败！")
        print("可能原因：")
        print("  1. TwelveData API额度已用完（免费版800次/天）")
        print("  2. 网络连接问题")
        print("  3. API Key无效")
        print("\n建议：")
        print("  - 等待明天API额度重置")
        print("  - 或升级到TwelveData付费计划")
        print("  - 检查网络连接和API Key配置")
        print("\n程序将继续运行，但可能无法获取数据...")
    
    print("=" * 60)
    print("开始实时监控...\n")
    
    # 主循环
    while True:
        try:
            # 遍历所有品种进行监控
            for symbol in SYMBOLS.keys():
                monitor_symbol(symbol, SYMBOLS[symbol], fetchers, last_alerts)
                # 品种间延迟，确保API请求间隔
                time.sleep(max(API_MIN_INTERVAL, 2))
            
            # 按配置间隔进行下一轮监控
            time.sleep(MONITOR_INTERVAL_SECONDS)
            
        except KeyboardInterrupt:
            print("\n收到停止信号，正在退出...")
            break
        except Exception as e:
            print(f"主循环异常: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(MONITOR_INTERVAL_SECONDS)

if __name__ == '__main__':
    # 设置Windows控制台UTF-8编码
    import sys
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    
    print("=" * 60)
    print("支撑阻力提醒系统启动中...")
    print("=" * 60)
    main()