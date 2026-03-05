import pandas as pd
import numpy as np
import time
import requests
import os
import json
import threading
from datetime import datetime, timedelta
from collections import deque

"""
增加微信企业启动通知，每次支撑阻力机会出现的时候通知机会：品种、价格、趋势方向等等重要信息，支撑多品种同时监控提醒，并且使用twelvedata数据源
"""

# ==================== 配置参数 ====================
# Twelve Data API配置
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "8ed68089e5114f4893927e4103bb5c6c")
TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

# 交易对配置（支持多品种）
# 格式：{'内部符号': 'TwelveData符号'}
SYMBOLS = {
    'USDJPY': 'USD/JPY',      # 美元/日元
    'EURUSD': 'EUR/USD',      # 欧元/美元
    'BTCUSD': 'BTC/USD',      # 比特币/美元
    'XAUUSD': 'XAU/USD',      # 黄金/美元
    'GBPUSD': 'GBP/USD',      # 英镑/美元
}

TIMEFRAMES = ['15min']  # 监控周期（twelvedata格式）- 仅使用15分钟数据
EMA_PERIODS = [20, 50, 100]            # EMA周期
SWING_WINDOW = 2                       # 摆动点识别窗口（左右各几根）
LEVEL_MERGE_THRESHOLD = 0.001          # 水平位合并阈值（0.1%）
NEAR_THRESHOLD = 0.001                 # 价格接近阈值（0.1%）
COOLDOWN_SECONDS = 900                 # 同一位置重复提醒冷却时间（秒）
WEWORK_WEBHOOK_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c3f76ed3-1f75-4288-afe0-60f7a217f128'  # 企业微信机器人Webhook地址

# 本地存储配置
MONITOR_DATA_DIR = "monitor_data"       # 监控数据存储目录
MONITOR_DATA_FILE = os.path.join(MONITOR_DATA_DIR, "monitor_records.json")  # 监控记录文件
DATA_RETENTION_DAYS = 30               # 数据保留天数（30天）

# API请求频率控制
API_MIN_INTERVAL = 1.0                 # 两次API请求之间的最小间隔（秒）
API_MAX_RETRIES = 5                    # 最大重试次数
API_RETRY_BASE_DELAY = 2.0             # 重试基础延迟（秒）
API_RETRY_MAX_DELAY = 60.0             # 重试最大延迟（秒）

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
        
        params = {
            'symbol': self.td_symbol,
            'interval': timeframe,
            'outputsize': self.limit,
            'apikey': TWELVEDATA_API_KEY,
            'format': 'JSON',
            'order': 'ASC',  # 升序，最新在末尾
        }
        
        # 指数退避重试机制
        for attempt in range(API_MAX_RETRIES):
            try:
                resp = requests.get(TWELVEDATA_URL, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                
                # 检查API错误
                if data.get('status') == 'error':
                    code = data.get('code', '')
                    msg = data.get('message', '')
                    
                    # API额度用尽 - 不重试
                    if 'run out of API credits' in msg or 'API credits' in msg:
                        print(f"  [TwelveData错误] {self.symbol} {timeframe}: API额度已用完")
                        print(f"  ⚠️ TwelveData API今日额度已用完，请等待明天或升级到付费计划")
                        return None
                    
                    # 429限流错误 - 使用指数退避重试
                    if str(code) == '429':
                        if attempt < API_MAX_RETRIES - 1:
                            # 指数退避：2^attempt * 基础延迟，但不超过最大延迟
                            delay = min(API_RETRY_BASE_DELAY * (2 ** attempt), API_RETRY_MAX_DELAY)
                            print(f"  [限流] {self.symbol} {timeframe}: 等待 {delay:.1f}秒后重试 (尝试 {attempt+1}/{API_MAX_RETRIES})")
                            time.sleep(delay)
                            continue
                        else:
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
        """获取当前最新成交价（使用最新K线的收盘价）"""
        if len(self.data['15min']) > 0:
            return self.data['15min'][-1]['close']
        # 如果15分钟数据不可用，尝试其他周期
        for tf in ['5min', '1h']:
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
    """基于15分钟数据判断趋势，返回 'bull'/'bear'/'neutral'"""
    trend_15m = check_trend(fetcher.ema.get('15min', {}))
    return trend_15m if trend_15m else 'neutral'

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
    ema20 = fetcher.ema.get('15min', {}).get('EMA20')
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
    """格式化支撑阻力机会汇总通知消息"""
    trend_emoji = {'bull': '🟢', 'bear': '🔴'}
    trend_name = {'bull': '看涨', 'bear': '看跌'}
    
    emoji = trend_emoji.get(trend, '')
    trend_text = trend_name.get(trend, '')
    
    # 按类型分组
    support_levels = [l for l in near_levels if l['type'] == 'support']
    resistance_levels = [l for l in near_levels if l['type'] == 'resistance']
    
    msg = f"""**📊 支撑阻力机会通知**

**品种**: {symbol}
**当前价格**: {format_price(price, symbol)}
**趋势方向**: {emoji} {trend_text}
**分析周期**: 15分钟

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
    
    # 反转形态
    if reversal_info and reversal_info[0]:
        pattern_name = reversal_info[1]
        pattern_cn = {
            'single_candle_hammer': '锤子线',
            'bullish_engulfing': '看涨吞没',
            'bearish_engulfing': '看跌吞没'
        }
        msg += f"**⚠️ 反转形态**: {pattern_cn.get(pattern_name, pattern_name)}\n\n"
    
    msg += f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    return msg

# ==================== 主监控循环 ====================
def send_startup_notification():
    """发送启动通知"""
    symbol_list = ', '.join(SYMBOLS.keys())
    msg = f"""**🚀 支撑阻力监控系统已启动**

**监控品种**: {symbol_list}
**分析周期**: 15分钟
**通知条件**: 仅在看涨/看跌趋势时通知，中性趋势不通知
**启动时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

系统将实时监控支撑阻力机会并推送汇总通知。"""
    
    send_wework_msg(msg, msgtype='markdown')
    print(f"[启动通知] 已发送，监控品种: {symbol_list}")

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

        # 获取15分钟K线DataFrame
        if len(fetcher.data['15min']) == 0:
            print(f"[{symbol}] 15分钟数据为空，跳过本次检查")
            return
            
        df_15m = pd.DataFrame(fetcher.data['15min'])

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
        
        # 如果有接近的位置且趋势不是中性，汇总发送通知并保存详细记录
        if near_levels and should_notify:
            # 格式化并发送汇总提醒
            msg = format_opportunity_msg_summary(symbol, price, trend, near_levels, reversal_info)
            send_wework_msg(msg, msgtype='markdown')
            
            # 保存详细监控记录（有接近位置的情况）
            save_monitor_record(symbol, price, trend, near_levels, reversal_info)
            
            # 控制台输出
            level_names = [l['name'] for l in near_levels]
            console_msg = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} | "
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
    
    # 初始化所有品种的数据获取器
    fetchers = {}
    for symbol, td_symbol in SYMBOLS.items():
        fetchers[symbol] = DataFetcher(symbol, td_symbol, TIMEFRAMES)
        print(f"[初始化] {symbol} -> {td_symbol}")
    
    # 每个品种的提醒记录
    last_alerts = {}  # 格式: {symbol_level_name_value: timestamp}
    
    # 发送启动通知
    send_startup_notification()
    
    print(f"\n开始监控 {len(SYMBOLS)} 个品种...")
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
            
            # 每分钟检查一次
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\n收到停止信号，正在退出...")
            break
        except Exception as e:
            print(f"主循环异常: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

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