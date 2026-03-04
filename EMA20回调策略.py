"""
Complete Price Action Trading Strategy Backtest
完整价格行为交易策略回测系统

基于您的策略文档实现:
1. 多周期趋势确认 (EMA20>EMA50>EMA100)
2. 突破识别 (前20根K线高低点)
3. 回调入场 (回踩EMA20)
4. 反转K线信号
5. 止损止盈规则
"""

import requests
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import warnings
warnings.filterwarnings('ignore')

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "61141e293ece4cad906e65413921b012")
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class CompleteStrategyBacktester:
    """完整策略回测引擎"""

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 initial_capital: float = 100000, risk_per_trade: float = 0.05,
                 charts_output_dir: str = None):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade  # 5%每笔
        self.df = None
        self.charts_output_dir = charts_output_dir or "trade_charts"
        os.makedirs(self.charts_output_dir, exist_ok=True)

    def load_data(self, api_key: str, timeframe: str = "5min") -> pd.DataFrame:
        """使用 TwelveData 加载并预处理日内数据"""
        symbol_map = {
            'eurusd': 'EUR/USD',
            'usdjpy': 'USD/JPY',
            'gbpusd': 'GBP/USD',
            'xauusd': 'XAU/USD',
            'xagusd': 'XAG/USD',
        }

        td_symbol = symbol_map.get(self.symbol.lower())
        if not td_symbol:
            raise ValueError(f"不支持的品种: {self.symbol}")

        if not api_key or api_key == "your_api_key_here":
            raise ValueError("请先设置有效的 TwelveData API Key")

        print(f"正在加载 {td_symbol} ({timeframe}) 日内数据...")

        base_url = "https://api.twelvedata.com/time_series"
        all_frames = []
        end_dt = None
        start_ts = pd.Timestamp(self.start_date)
        end_ts = pd.Timestamp(self.end_date)

        for batch in range(3):
            params = {
                'symbol': td_symbol,
                'interval': timeframe,
                'outputsize': 5000,
                'apikey': api_key,
                'format': 'JSON',
                'timezone': 'UTC',
                'order': 'DESC',
            }
            if end_dt is not None:
                params['end_date'] = end_dt.strftime('%Y-%m-%d %H:%M:%S')

            data = self._fetch_twelvedata_with_retry(base_url, params)

            values = data.get('values', [])
            if not values:
                break

            batch_df = pd.DataFrame(values)
            batch_df['datetime'] = pd.to_datetime(batch_df['datetime'])
            batch_df = batch_df.set_index('datetime')
            batch_df = batch_df.rename(columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            })

            for col in ['Open', 'High', 'Low', 'Close']:
                batch_df[col] = pd.to_numeric(batch_df[col], errors='coerce')

            all_frames.append(batch_df)
            oldest_dt = batch_df.index.min()
            newest_dt = batch_df.index.max()
            print(f"  第{batch + 1}批: {len(batch_df)} 根K线 ({oldest_dt} ~ {newest_dt})")

            if oldest_dt <= start_ts:
                break

            end_dt = oldest_dt.to_pydatetime() - timedelta(minutes=1)
            # 免费额度有每分钟限制，批次间稍作等待，降低触发限流概率
            time.sleep(8)

        if not all_frames:
            raise ValueError("无法获取数据")

        self.df = pd.concat(all_frames)
        self.df = self.df[~self.df.index.duplicated(keep='first')]
        self.df = self.df.sort_index()
        self.df = self.df[(self.df.index >= start_ts) & (self.df.index <= end_ts)]

        if len(self.df) == 0:
            raise ValueError("指定时间范围内无可用数据")

        # 计算均线系统
        self.df['ema_20'] = self.df['Close'].ewm(span=20, adjust=False).mean()
        self.df['ema_50'] = self.df['Close'].ewm(span=50, adjust=False).mean()
        self.df['ema_100'] = self.df['Close'].ewm(span=100, adjust=False).mean()

        # K线属性
        self.df['range'] = self.df['High'] - self.df['Low']
        self.df['body'] = abs(self.df['Close'] - self.df['Open'])
        self.df['body_ratio'] = self.df['body'] / self.df['range']

        # 影线
        self.df['upper_shadow'] = np.where(
            self.df['Close'] > self.df['Open'],
            self.df['High'] - self.df['Close'],
            self.df['High'] - self.df['Open']
        )
        self.df['lower_shadow'] = np.where(
            self.df['Close'] > self.df['Open'],
            self.df['Open'] - self.df['Low'],
            self.df['Close'] - self.df['Low']
        )

        # 趋势K线定义: 实体 > 影线之和 * 2
        self.df['is_trend_bar'] = self.df['body'] > (self.df['upper_shadow'] + self.df['lower_shadow']) * 2

        # K线方向
        self.df['direction'] = np.where(self.df['Close'] > self.df['Open'], 1, -1)

        # ATR
        self.df['atr'] = self.df['range'].rolling(14).mean()

        # 前20根K线高低点
        self.df['high_20'] = self.df['High'].rolling(20).max()
        self.df['low_20'] = self.df['Low'].rolling(20).min()

        print(f"数据加载完成: {len(self.df)} 根K线")
        return self.df

    def _fetch_twelvedata_with_retry(self, url: str, params: dict,
                                     max_retry: int = 6) -> dict:
        """请求 TwelveData，并在分钟限流时自动等待重试"""
        for attempt in range(max_retry):
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                if attempt == max_retry - 1:
                    raise ValueError(f"TwelveData 请求失败: {e}") from e
                wait_s = min(5 * (attempt + 1), 20)
                print(f"  请求异常，{wait_s}秒后重试: {e}")
                time.sleep(wait_s)
                continue

            if data.get('status') != 'error':
                return data

            msg = str(data.get('message', '未知错误'))
            msg_lower = msg.lower()
            if 'run out of api credits for the current minute' in msg_lower or \
               'wait for the next minute' in msg_lower:
                now = datetime.now()
                wait_s = (60 - now.second) + 2
                print(f"  触发分钟限流，等待 {wait_s} 秒后重试...")
                time.sleep(wait_s)
                continue

            if 'apikey' in msg_lower or 'invalid' in msg_lower:
                raise ValueError(f"TwelveData API错误: {msg}（请检查 API Key）")

            if 'limit' in msg_lower:
                raise ValueError(f"TwelveData API错误: {msg}（今日额度可能已用尽）")

            raise ValueError(f"TwelveData API错误: {msg}")

        raise ValueError("TwelveData 请求重试后仍失败")

    def check_trend(self, idx: int) -> str:
        """检查多周期趋势 (简化为单周期)"""
        if idx < 100:
            return None

        ema20 = self.df['ema_20'].iloc[idx]
        ema50 = self.df['ema_50'].iloc[idx]
        ema100 = self.df['ema_100'].iloc[idx]
        close = self.df['Close'].iloc[idx]

        # 均线斜率
        ema20_slope = ema20 > self.df['ema_20'].iloc[idx-1]
        ema50_slope = ema50 > self.df['ema_50'].iloc[idx-1]
        ema100_slope = ema100 > self.df['ema_100'].iloc[idx-1]

        # 多头: EMA20>EMA50>EMA100 且斜率为正
        if ema20 > ema50 > ema100 and ema20_slope and ema50_slope and ema100_slope:
            return 'uptrend'

        # 空头: EMA20<EMA50<EMA100 且斜率为负
        if ema20 < ema50 < ema100 and not ema20_slope and not ema50_slope and not ema100_slope:
            return 'downtrend'

        return None

    def check_breakout(self, idx: int, trend: str) -> bool:
        """检查是否突破前20根K线高低点"""
        if idx < 25:
            return False

        current_high = self.df['High'].iloc[idx]
        current_low = self.df['Low'].iloc[idx]
        prev_high_20 = self.df['high_20'].iloc[idx-1]
        prev_low_20 = self.df['low_20'].iloc[idx-1]

        if trend == 'uptrend':
            # 向上突破前20根高点
            return current_high > prev_high_20 and self.df['is_trend_bar'].iloc[idx]
        else:
            # 向下突破前20根低点
            return current_low < prev_low_20 and self.df['is_trend_bar'].iloc[idx]

    def check_pullback(self, idx: int, trend: str) -> bool:
        """检查是否回调 (价格未创新高/新低)"""
        if idx < 10:
            return False

        if trend == 'uptrend':
            # 上涨中未创新高
            current_high = self.df['High'].iloc[idx]
            recent_high = self.df['High'].iloc[idx-10:idx].max()
            return current_high < recent_high
        else:
            current_low = self.df['Low'].iloc[idx]
            recent_low = self.df['Low'].iloc[idx-10:idx].min()
            return current_low > recent_low

    def check_pullback_end(self, idx: int, trend: str) -> bool:
        """检查回调是否结束 (回踩EMA20)"""
        if idx < 25:
            return False

        close = self.df['Close'].iloc[idx]
        ema20 = self.df['ema_20'].iloc[idx]
        close_1 = self.df['Close'].iloc[idx-1]
        ema20_1 = self.df['ema_20'].iloc[idx-1]

        if trend == 'uptrend':
            # 多头: 价格站上EMA20
            return close > ema20 and close_1 <= ema20_1
        else:
            # 空头: 价格跌破EMA20
            return close < ema20 and close_1 >= ema20_1

    def check_reversal_candle(self, idx: int, trend: str) -> bool:
        """检查反转K线信号"""
        if idx < 2:
            return False

        # 单K线反转 (锤子线/射击星)
        current = self.df.iloc[idx]
        prev = self.df.iloc[idx-1]

        if trend == 'uptrend':
            # 多头反转: 长下影线 + 短上影线 + 阳线
            lower_shadow_ratio = current['lower_shadow'] / current['range']
            upper_shadow_ratio = current['upper_shadow'] / current['range']

            return (lower_shadow_ratio >= 0.5 and
                    upper_shadow_ratio <= 0.1 and
                    current['direction'] == 1 and
                    current['is_trend_bar'])
        else:
            # 空头反转: 长上影线 + 短下影线 + 阴线
            upper_shadow_ratio = current['upper_shadow'] / current['range']
            lower_shadow_ratio = current['lower_shadow'] / current['range']

            return (upper_shadow_ratio >= 0.5 and
                    lower_shadow_ratio <= 0.1 and
                    current['direction'] == -1 and
                    current['is_trend_bar'])

    def check_follower_quality(self, idx: int, trend: str) -> bool:
        """检查跟随K线质量 (突破后1-2根K线为趋势K线)"""
        if idx >= len(self.df) - 3:
            return False

        # 检查接下来1-2根K线
        for i in range(1, 3):
            if idx + i >= len(self.df):
                break
            bar = self.df.iloc[idx + i]
            if bar['is_trend_bar'] and bar['direction'] == (1 if trend == 'uptrend' else -1):
                return True

        return False

    def find_signals(self) -> list:
        """寻找所有信号"""
        signals = []

        for idx in range(150, len(self.df) - 10):
            trend = self.check_trend(idx)
            if trend is None:
                continue

            # 1. 检查突破
            if not self.check_breakout(idx, trend):
                continue

            # 2. 检查突破后是否回调
            in_pullback = False
            pullback_start = None

            for i in range(idx + 1, min(idx + 25, len(self.df))):
                if self.check_pullback(i, trend):
                    in_pullback = True
                    pullback_start = i
                    break

            if not in_pullback:
                continue  # 没有回调,跳过

            # 3. 检查回调是否结束 (回踩EMA20)
            for i in range(pullback_start, min(pullback_start + 20, len(self.df) - 1)):
                if self.check_pullback_end(i, trend):
                    # 4. 检查是否有反转K线信号
                    if self.check_reversal_candle(i, trend):
                        # 5. 检查跟随K线质量
                        if self.check_follower_quality(i, trend):
                            signals.append({
                                'idx': i,
                                'trend': trend,
                                'type': 'reversal',
                                'entry': self.df['Close'].iloc[i],
                                'stop': self.find_stop_loss(i, trend),
                                'date': self.df.index[i]
                            })
                            break
                    # 或者检查是否出现强力趋势K线
                    elif self.df['is_trend_bar'].iloc[i] and self.df['direction'].iloc[i] == (1 if trend == 'uptrend' else -1):
                        if self.check_follower_quality(i, trend):
                            signals.append({
                                'idx': i,
                                'trend': trend,
                                'type': 'trend_follow',
                                'entry': self.df['Close'].iloc[i],
                                'stop': self.find_stop_loss(i, trend),
                                'date': self.df.index[i]
                            })
                            break

        return signals

    def find_stop_loss(self, idx: int, trend: str) -> float:
        """寻找止损位置 (前一个显著低点/高点)，最大距离不超过 2*ATR"""
        lookback = min(50, idx)
        bars = self.df.iloc[idx-lookback:idx]
        atr = self.df['atr'].iloc[idx]
        entry = self.df['Close'].iloc[idx]
        max_stop_distance = 2.0 * atr  # 止损最大 2*ATR

        if trend == 'uptrend':
            raw_stop = bars['Low'].min() - atr * 0.5
            stop_distance = entry - raw_stop
            if stop_distance > max_stop_distance:
                stop_distance = max_stop_distance
            return entry - stop_distance
        else:
            raw_stop = bars['High'].max() + atr * 0.5
            stop_distance = raw_stop - entry
            if stop_distance > max_stop_distance:
                stop_distance = max_stop_distance
            return entry + stop_distance

    def find_take_profit(self, idx: int, entry: float, stop: float, trend: str) -> tuple:
        """计算止盈位置"""
        risk = abs(entry - stop)

        # 第一目标: 前高/前低
        lookback = min(50, idx)
        bars = self.df.iloc[idx-lookback:idx]

        if trend == 'uptrend':
            tp1 = bars['High'].max()
            tp2 = entry + risk * 2
        else:
            tp1 = bars['Low'].min()
            tp2 = entry - risk * 2

        return tp1, tp2

    @staticmethod
    def _safe_symbol_name(symbol: str) -> str:
        return symbol.replace("/", "_").replace("\\", "_").replace(":", "_")

    def plot_trade_chart(self, trade: dict, trade_no: int) -> str:
        """为单笔交易绘制K线图，并标记入场/出场"""
        entry_idx = trade['entry_idx']
        exit_idx = trade['exit_idx']
        left = max(0, entry_idx - 80)
        right = min(len(self.df), max(exit_idx + 30, entry_idx + 40))
        plot_df = self.df.iloc[left:right].copy()

        fig, ax = plt.subplots(figsize=(13, 6))
        x = mdates.date2num(plot_df.index.to_pydatetime())
        candle_width = (5 / (24 * 60)) * 0.7  # 5分钟K线宽度

        for i, (_, row) in enumerate(plot_df.iterrows()):
            color = '#2ca02c' if row['Close'] >= row['Open'] else '#d62728'
            ax.vlines(x[i], row['Low'], row['High'], color=color, linewidth=1.0, alpha=0.9)
            body_low = min(row['Open'], row['Close'])
            body_h = max(abs(row['Close'] - row['Open']), 1e-8)
            rect = Rectangle((x[i] - candle_width / 2, body_low), candle_width, body_h,
                             facecolor=color, edgecolor=color, linewidth=0.8, alpha=0.9)
            ax.add_patch(rect)

        entry_time = pd.Timestamp(trade['entry_time'])
        exit_time = pd.Timestamp(trade['exit_time'])
        ax.scatter(mdates.date2num(entry_time), trade['entry_price'],
                   marker='^', s=120, color='blue', label='入场')
        ax.scatter(mdates.date2num(exit_time), trade['exit_price'],
                   marker='v', s=120, color='black', label='出场')

        ax.axhline(trade['entry_price'], color='blue', linestyle='--', linewidth=1.0, alpha=0.6)
        ax.axhline(trade['exit_price'], color='black', linestyle='--', linewidth=1.0, alpha=0.6)
        ax.grid(True, alpha=0.2)
        ax.legend(loc='upper left')
        ax.set_title(
            f"{trade['symbol_display']} 交易#{trade_no} | {trade['direction']} | "
            f"盈亏: {trade['pnl']:.2f} | R: {trade['r_multiple']:.2f}"
        )
        ax.set_ylabel("价格")
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        fig.autofmt_xdate()
        plt.tight_layout()

        symbol_safe = self._safe_symbol_name(trade['symbol_display'])
        chart_name = f"{trade_no:04d}_{symbol_safe}_{entry_time.strftime('%Y%m%d_%H%M')}.png"
        chart_path = os.path.join(self.charts_output_dir, chart_name)
        fig.savefig(chart_path, dpi=140)
        plt.close(fig)
        return chart_path

    def run_backtest(self, signals: list, symbol_display: str = None, save_charts: bool = True) -> dict:
        """执行回测"""
        print(f"\n找到 {len(signals)} 个信号,开始回测...")

        capital = self.initial_capital
        trades = []
        symbol_display = symbol_display or self.symbol.upper()

        for trade_no, signal in enumerate(signals, start=1):
            idx = signal['idx']
            entry = signal['entry']
            stop = signal['stop']
            initial_stop = stop
            direction = signal['trend']

            # 计算仓位 (5%风险)
            risk = capital * self.risk_per_trade
            stop_distance = abs(entry - stop)
            if stop_distance == 0:
                continue

            size = risk / stop_distance

            # 计算止盈
            tp1, tp2 = self.find_take_profit(idx, entry, stop, direction)

            # 模拟交易（止损最大2*ATR，利润回撤30%强制离场）
            future_bars = self.df.iloc[idx:min(idx + 30, len(self.df))]
            profit_drawdown_ratio = 0.70   # 利润回撤30%即保留70%时强制离场

            entry_triggered = False
            entry_price = None
            entry_time = None
            tp1_hit = False
            exit_price = None
            exit_reason = None
            exit_time = None
            max_pnl_seen = None  # 曾达到的最大浮动盈亏，用于利润回撤判断

            for bar_idx, (i, bar) in enumerate(future_bars.iterrows()):
                if not entry_triggered:
                    # 市价入场
                    entry_triggered = True
                    entry_price = bar['Close']
                    entry_time = i
                    continue

                if direction == 'uptrend':
                    # 先检查止损（优先）
                    if bar['Low'] <= stop:
                        exit_price = stop
                        exit_reason = 'SL'
                        exit_time = i
                        break

                    # 检查止盈2
                    if bar['High'] >= tp2:
                        exit_price = tp2
                        exit_reason = 'TP2'
                        exit_time = i
                        break

                    # 止盈1 移动止损到盈亏平衡
                    if not tp1_hit and bar['High'] >= tp1:
                        tp1_hit = True
                        stop = entry_price

                    # 按收盘价更新最大浮动盈亏，并检查利润回撤30%强制离场
                    unrealized_pnl = (bar['Close'] - entry_price) * size
                    if max_pnl_seen is None:
                        max_pnl_seen = unrealized_pnl
                    else:
                        max_pnl_seen = max(max_pnl_seen, unrealized_pnl)
                    if max_pnl_seen > 0 and unrealized_pnl < max_pnl_seen * profit_drawdown_ratio:
                        exit_price = bar['Close']
                        exit_reason = 'DRAWDOWN'
                        exit_time = i
                        break

                else:  # downtrend
                    if bar['High'] >= stop:
                        exit_price = stop
                        exit_reason = 'SL'
                        exit_time = i
                        break

                    if bar['Low'] <= tp2:
                        exit_price = tp2
                        exit_reason = 'TP2'
                        exit_time = i
                        break

                    if not tp1_hit and bar['Low'] <= tp1:
                        tp1_hit = True
                        stop = entry_price

                    unrealized_pnl = (entry_price - bar['Close']) * size
                    if max_pnl_seen is None:
                        max_pnl_seen = unrealized_pnl
                    else:
                        max_pnl_seen = max(max_pnl_seen, unrealized_pnl)
                    if max_pnl_seen > 0 and unrealized_pnl < max_pnl_seen * profit_drawdown_ratio:
                        exit_price = bar['Close']
                        exit_reason = 'DRAWDOWN'
                        exit_time = i
                        break

                # 时间止损
                if bar_idx > 20:
                    exit_price = bar['Close']
                    exit_reason = 'TIME'
                    exit_time = i
                    break

            if exit_price is not None and entry_price is not None and entry_time is not None:
                pnl = (exit_price - entry_price) * size if direction == 'uptrend' else (entry_price - exit_price) * size
                capital += pnl
                r_multiple = pnl / risk if risk != 0 else 0.0
                result = 'WIN' if pnl > 0 else 'LOSS'
                entry_idx = self.df.index.get_indexer([entry_time])[0]
                exit_idx = self.df.index.get_indexer([exit_time])[0]

                trade_info = {
                    'symbol': self.symbol,
                    'symbol_display': symbol_display,
                    'type': signal['type'],
                    'direction': direction,
                    'signal_time': str(signal['date']),
                    'entry_time': str(entry_time),
                    'exit_time': str(exit_time),
                    'entry': entry_price,
                    'exit': exit_price,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'initial_stop': initial_stop,
                    'risk_amount': risk,
                    'pnl': pnl,
                    'r_multiple': r_multiple,
                    'result': result,
                    'exit_reason': exit_reason,
                    'entry_idx': entry_idx,
                    'exit_idx': exit_idx,
                    'chart_file': ''
                }

                if save_charts:
                    try:
                        trade_info['chart_file'] = self.plot_trade_chart(trade_info, trade_no)
                    except Exception as e:
                        print(f"  交易图表生成失败 #{trade_no}: {e}")

                trades.append(trade_info)

        return self.calculate_stats(trades)

    def calculate_stats(self, trades: list) -> dict:
        """计算统计"""
        if not trades:
            return {'total_trades': 0}

        df = pd.DataFrame(trades)
        wins = df[df['pnl'] > 0]
        losses = df[df['pnl'] <= 0]

        return {
            'total_trades': len(trades),
            'win_rate': len(wins) / len(df) * 100,
            'total_pnl': df['pnl'].sum(),
            'avg_win': wins['pnl'].mean() if len(wins) > 0 else 0,
            'avg_loss': abs(losses['pnl'].mean()) if len(losses) > 0 else 0,
            'profit_factor': (wins['pnl'].sum() / abs(losses['pnl'].sum())) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0,
            'final_capital': self.initial_capital + df['pnl'].sum(),
            'trades': trades
        }


def run_complete_strategy_test():
    """运行完整策略测试（TwelveData 日内回测）"""

    SYMBOLS = {
        'EUR/USD': 'eurusd',
        'USD/JPY': 'usdjpy',
        'GBP/USD': 'gbpusd',
        'XAU/USD': 'xauusd',
        'XAG/USD': 'xagusd',
    }
    END_DATE = datetime.now().strftime("%Y-%m-%d")
    START_DATE = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    API_KEY = TWELVEDATA_API_KEY

    print("="*60)
    print("EMA20回调策略回测（TwelveData 日内数据）")
    print("="*60)
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print("回测周期: 5min")
    print("="*60)

    all_results = []
    all_trade_details = []
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join("backtest_outputs", f"ema20_{run_tag}")
    charts_dir = os.path.join(output_dir, "trade_charts")
    os.makedirs(charts_dir, exist_ok=True)
    excel_path = os.path.join(output_dir, f"交易明细_{run_tag}.xlsx")
    print(f"输出目录: {output_dir}")

    for idx, (name, symbol) in enumerate(SYMBOLS.items()):
        print(f"\n{'='*40}")
        print(f"测试: {name} ({symbol})")
        print(f"{'='*40}")

        try:
            if idx > 0:
                time.sleep(1)

            bt = CompleteStrategyBacktester(
                symbol=symbol,
                start_date=START_DATE,
                end_date=END_DATE,
                initial_capital=100000,
                risk_per_trade=0.05,
                charts_output_dir=charts_dir
            )
            bt.load_data(API_KEY, "5min")
            signals = bt.find_signals()
            print(f"信号数: {len(signals)}")

            result = bt.run_backtest(signals, symbol_display=name, save_charts=True)

            if result['total_trades'] > 0:
                print(f"\n--- {name} 结果 ---")
                print(f"交易次数: {result['total_trades']}")
                print(f"胜率: {result['win_rate']:.2f}%")
                print(f"总盈亏: ${result['total_pnl']:,.2f}")
                print(f"盈利因子: {result['profit_factor']:.2f}")

                all_results.append({'symbol': name, **result})
                all_trade_details.extend(result.get('trades', []))

        except Exception as e:
            print(f"错误: {e}")

    # 汇总
    print("\n" + "="*60)
    print("汇总结果")
    print("="*60)
    print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'总盈亏':>12} {'盈利因子':>10}")
    print("-"*60)

    for r in all_results:
        print(f"{r['symbol']:<10} {r['total_trades']:>8} {r['win_rate']:>7.1f}% ${r['total_pnl']:>10,.0f} {r['profit_factor']:>10.2f}")

    if all_trade_details:
        trade_df = pd.DataFrame(all_trade_details)
        trade_export = pd.DataFrame({
            '品种': trade_df['symbol_display'],
            '方向': trade_df['direction'],
            '信号类型': trade_df['type'],
            '入场日期': trade_df['entry_time'],
            '入场价格': trade_df['entry_price'],
            '出场日期': trade_df['exit_time'],
            '出场价格': trade_df['exit_price'],
            '盈亏金额': trade_df['pnl'],
            '盈亏比(R)': trade_df['r_multiple'],
            '结果': trade_df['result'],
            '出场原因': trade_df['exit_reason'],
            '图表文件': trade_df['chart_file']
        })
        summary_df = pd.DataFrame(all_results)[['symbol', 'total_trades', 'win_rate', 'total_pnl', 'profit_factor', 'final_capital']]
        summary_df = summary_df.rename(columns={
            'symbol': '品种',
            'total_trades': '交易数',
            'win_rate': '胜率(%)',
            'total_pnl': '总盈亏',
            'profit_factor': '盈利因子',
            'final_capital': '期末资金'
        })
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            trade_export.to_excel(writer, sheet_name='交易明细', index=False)
            summary_df.to_excel(writer, sheet_name='汇总', index=False)
        print(f"\n交易明细已导出: {excel_path}")
        print(f"每笔K线图目录: {charts_dir}")

    return all_results


if __name__ == "__main__":
    if TWELVEDATA_API_KEY == "your_api_key_here":
        print("请先设置 TwelveData API Key：")
        print("1) 访问 https://twelvedata.com/apikey")
        print("2) 在环境变量设置 TWELVEDATA_API_KEY")
        print("   或直接修改脚本顶部 TWELVEDATA_API_KEY 变量")
    else:
        results = run_complete_strategy_test()
