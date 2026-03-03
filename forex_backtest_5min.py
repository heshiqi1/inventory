"""
H1/H2 Price Action Strategy - 5分钟周期回测
使用最近60天的5分钟数据回测 forex_monitor_wx.py 策略
策略: 双EMA排列 + RSI过滤 + H1/H2形态 + 分批止盈
数据源: Twelve Data (免费，800次/天)
  - 注册免费 API Key: https://twelvedata.com/apikey
  - 支持: EURUSD、USDJPY、GBPUSD、XAUUSD、XAGUSD
"""

import requests
import io
import time
import pandas as pd
import numpy as np
import warnings
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
import os
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')

# ============================================================
# ★ 请在此填入你的 Twelve Data 免费 API Key
#   注册地址: https://twelvedata.com/apikey  (免费, 无需绑卡)
# ============================================================
TWELVEDATA_API_KEY = "61141e293ece4cad906e65413921b012"

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class Forex5MinBacktester:
    """外汇5分钟周期回测引擎"""

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 initial_capital: float = 100000, risk_per_trade: float = 0.02,
                 chart_output_dir: str = None):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.df = None
        self.chart_output_dir = chart_output_dir or "backtest_charts_5min"
        
        if not os.path.exists(self.chart_output_dir):
            os.makedirs(self.chart_output_dir)

    def load_data_twelvedata(self, api_key: str) -> pd.DataFrame:
        """
        使用 Twelve Data API 加载5分钟数据
        - 免费 800次/天，支持外汇 + 贵金属
        - 注册: https://twelvedata.com/apikey
        - 分3批次请求，每批5000根K线，覆盖约60天
        """
        # 符号映射: 内部名 -> Twelve Data 符号
        symbol_map = {
            'eurusd': 'EUR/USD',
            'usdjpy': 'USD/JPY',
            'gbpusd': 'GBP/USD',
            'xauusd': 'XAU/USD',
            'xagusd': 'XAG/USD',
        }
        td_symbol = symbol_map.get(self.symbol.lower())
        if not td_symbol:
            print(f"  不支持的品种: {self.symbol}")
            return None

        print(f"正在使用 Twelve Data 加载 {td_symbol} 5分钟数据...")

        if not api_key or api_key == "your_api_key_here":
            print("  ✗ 未设置 API Key！")
            print("  请到 https://twelvedata.com/apikey 免费注册")
            print("  然后将 Key 填入脚本顶部的 TWELVEDATA_API_KEY 变量")
            return None

        base_url = "https://api.twelvedata.com/time_series"
        all_frames = []
        end_dt = None  # 第一次不设 end_date，获取最新数据

        for batch in range(3):
            params = {
                'symbol':     td_symbol,
                'interval':   '5min',
                'outputsize': 5000,
                'apikey':     api_key,
                'format':     'JSON',
                'timezone':   'UTC',
                'order':      'DESC',   # 最新优先，便于分页
            }
            if end_dt:
                params['end_date'] = end_dt.strftime('%Y-%m-%d %H:%M:%S')

            try:
                resp = requests.get(base_url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  第{batch+1}批请求失败: {e}")
                break

            # 检查 API 错误
            if data.get('status') == 'error':
                msg = data.get('message', '')
                print(f"  API 错误: {msg}")
                if 'apikey' in msg.lower() or 'invalid' in msg.lower():
                    print("  请检查 API Key 是否正确")
                elif 'limit' in msg.lower():
                    print("  今日请求次数已达上限（免费版800次/天），明天再试")
                return None

            values = data.get('values', [])
            if not values:
                print(f"  第{batch+1}批无更多数据，停止分页")
                break

            batch_df = pd.DataFrame(values)
            batch_df['datetime'] = pd.to_datetime(batch_df['datetime'])
            batch_df = batch_df.set_index('datetime')
            batch_df = batch_df.rename(columns={
                'open': 'Open', 'high': 'High',
                'low': 'Low', 'close': 'Close',
                'volume': 'Volume'
            })
            for col in ['Open', 'High', 'Low', 'Close']:
                batch_df[col] = pd.to_numeric(batch_df[col], errors='coerce')

            all_frames.append(batch_df)
            oldest_dt = batch_df.index.min()
            print(f"  第{batch+1}批: {len(batch_df)} 根K线 "
                  f"({oldest_dt.date()} ~ {batch_df.index.max().date()})")

            # 若已覆盖 start_date，停止分页
            target_start = datetime.now() - timedelta(days=62)
            if oldest_dt <= pd.Timestamp(target_start, tz=oldest_dt.tzinfo):
                break

            # 下一批的 end_date = 本批最旧时间 - 1分钟
            end_dt = oldest_dt.to_pydatetime() - timedelta(minutes=1)
            # 每批间隔1秒，避免触发频率限制
            time.sleep(1)

        if not all_frames:
            print(f"  {self.symbol} 数据获取失败，无任何数据返回")
            return None

        combined = pd.concat(all_frames)
        combined = combined[~combined.index.duplicated(keep='first')]
        combined = combined.sort_index()

        # 只保留 start_date 以后的数据
        start_ts = pd.Timestamp(self.start_date)
        if combined.index.tz is not None:
            start_ts = start_ts.tz_localize(combined.index.tz)
        combined = combined[combined.index >= start_ts]

        self.df = combined
        total_days = (self.df.index.max() - self.df.index.min()).days
        print(f"  ✓ 合并完成: {len(self.df)} 根K线 "
              f"({self.df.index[0].date()} ~ {self.df.index[-1].date()}, "
              f"约{total_days}天)")

        self._calculate_indicators()
        return self.df

    def _calculate_indicators(self):
        """计算所有技术指标（与 forex_monitor_wx.py 完全一致）"""
        
        # 双EMA趋势指标
        self.df['ema_20'] = self.df['Close'].ewm(span=20, adjust=False).mean()
        self.df['ema_50'] = self.df['Close'].ewm(span=50, adjust=False).mean()

        # RSI(14) 过滤器
        delta = self.df['Close'].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        self.df['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        self.df['rsi'] = self.df['rsi'].fillna(50)

        # K线属性
        self.df['range'] = self.df['High'] - self.df['Low']
        self.df['body'] = abs(self.df['Close'] - self.df['Open'])
        self.df['body_ratio'] = self.df['body'] / self.df['range'].replace(0, np.nan)
        self.df['body_ratio'] = self.df['body_ratio'].fillna(0)
        self.df['is_trend_bar'] = self.df['body_ratio'] >= 0.5
        self.df['direction'] = np.where(self.df['Close'] > self.df['Open'], 1, -1)

        # ATR
        self.df['atr'] = self.df['range'].rolling(14).mean()

    def detect_trend(self, idx: int) -> str:
        """双EMA排列趋势检测（与 forex_monitor_wx.py 完全一致）"""
        if idx < 50:
            return None

        ema_20 = self.df['ema_20'].iloc[idx]
        ema_50 = self.df['ema_50'].iloc[idx]
        close_price = self.df['Close'].iloc[idx]
        ema_slope = (self.df['ema_20'].iloc[idx] - self.df['ema_20'].iloc[idx-5]) / self.df['ema_20'].iloc[idx-5]

        if close_price > ema_20 and ema_20 > ema_50 and ema_slope > 0.0003:
            return 'uptrend'
        elif close_price < ema_20 and ema_20 < ema_50 and ema_slope < -0.0003:
            return 'downtrend'

        return None

    def is_momentum_confirmed(self, idx: int, trend: str) -> bool:
        """宽松动量确认（与 forex_monitor_wx.py 完全一致）"""
        if idx < 5:
            return False

        recent = self.df.iloc[idx-4:idx]
        if len(recent) < 3:
            return False

        directions = recent['direction'].values
        trend_bars = recent['is_trend_bar'].values

        # 条件A: 连续2根趋势K线
        if len(directions) >= 2:
            if directions[-1] == directions[-2] and trend_bars[-1] and trend_bars[-2]:
                return True

        # 条件B: 近3根bar收盘价有方向性动量
        closes = self.df['Close'].iloc[idx-3:idx].values
        if trend == 'uptrend' and closes[-1] > closes[0]:
            return True
        if trend == 'downtrend' and closes[-1] < closes[0]:
            return True

        return False

    def find_signals(self) -> list:
        """寻找信号（与 forex_monitor_wx.py 完全一致）"""
        signals = []
        last_signal_idx = -10

        for idx in range(50, len(self.df) - 10):
            trend = self.detect_trend(idx)

            if trend is None:
                continue

            if not self.is_momentum_confirmed(idx, trend):
                continue

            # RSI过滤：避免追高杀低
            rsi = self.df['rsi'].iloc[idx]
            if trend == 'uptrend' and rsi > 70:
                continue
            if trend == 'downtrend' and rsi < 30:
                continue

            if idx - last_signal_idx < 5:
                continue

            if trend == 'uptrend':
                signal = self.check_bull_signal(idx)
            else:
                signal = self.check_bear_signal(idx)

            if signal:
                signal['trend'] = trend
                signal['rsi'] = rsi
                signals.append(signal)
                last_signal_idx = signal['idx']

        return signals

    def check_bull_signal(self, idx: int) -> dict:
        """多头信号检测 H1/H2（与 forex_monitor_wx.py 完全一致）"""
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if self.df['High'].iloc[i] < self.df['High'].iloc[i+1]:
                for j in range(i, min(i + 15, len(self.df) - 1)):
                    if self.df['High'].iloc[j] > self.df['High'].iloc[j-1]:
                        if self.df['Close'].iloc[j] > self.df['Open'].iloc[j]:
                            if self.df['body_ratio'].iloc[j] >= 0.3:
                                atr = self.df['atr'].iloc[j]
                                slip = atr * 0.01 if pd.notna(atr) else 0.0001
                                return {
                                    'idx': j,
                                    'type': 'H1',
                                    'direction': 'long',
                                    'entry': self.df['High'].iloc[j] + slip,
                                    'stop': self.df['Low'].iloc[j] - slip,
                                    'date': self.df.index[j]
                                }
                break
        return None

    def check_bear_signal(self, idx: int) -> dict:
        """空头信号检测 L1/L2（与 forex_monitor_wx.py 完全一致）"""
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if self.df['Low'].iloc[i] > self.df['Low'].iloc[i+1]:
                for j in range(i, min(i + 15, len(self.df) - 1)):
                    if self.df['Low'].iloc[j] < self.df['Low'].iloc[j-1]:
                        if self.df['Close'].iloc[j] < self.df['Open'].iloc[j]:
                            if self.df['body_ratio'].iloc[j] >= 0.3:
                                atr = self.df['atr'].iloc[j]
                                slip = atr * 0.01 if pd.notna(atr) else 0.0001
                                return {
                                    'idx': j,
                                    'type': 'L1',
                                    'direction': 'short',
                                    'entry': self.df['Low'].iloc[j] - slip,
                                    'stop': self.df['High'].iloc[j] + slip,
                                    'date': self.df.index[j]
                                }
                break
        return None

    def plot_trade_chart(self, trade_info: dict, trade_num: int):
        """绘制单个交易的K线图"""
        idx = trade_info['entry_idx']
        # 5分钟周期显示前后100根K线
        start_idx = max(0, idx - 100)
        end_idx = min(len(self.df), trade_info['exit_idx'] + 30)
        
        chart_data = self.df.iloc[start_idx:end_idx].copy()
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 10), 
                                        gridspec_kw={'height_ratios': [3, 1]})
        
        # 绘制K线（只显示部分以提高性能）
        step = max(1, len(chart_data) // 500)  # 最多显示500根K线
        chart_data_display = chart_data.iloc[::step]
        
        for i in range(len(chart_data_display)):
            date = chart_data_display.index[i]
            open_price = chart_data_display['Open'].iloc[i]
            high = chart_data_display['High'].iloc[i]
            low = chart_data_display['Low'].iloc[i]
            close = chart_data_display['Close'].iloc[i]
            
            color = 'red' if close >= open_price else 'green'
            ax1.plot([date, date], [low, high], color=color, linewidth=0.6)
            ax1.add_patch(plt.Rectangle((mdates.date2num(date) - 0.3, min(open_price, close)),
                                        0.6, abs(close - open_price),
                                        facecolor=color, edgecolor=color, alpha=0.7))
        
        # 绘制EMA
        ax1.plot(chart_data.index, chart_data['ema_20'], 'b-', linewidth=1.2, 
                label='EMA20', alpha=0.7)
        ax1.plot(chart_data.index, chart_data['ema_50'], 'orange', linewidth=1.2,
                label='EMA50', alpha=0.7)
        
        # 标注入场位置
        entry_date = self.df.index[trade_info['entry_idx']]
        entry_price = trade_info['entry_price']
        
        entry_reason = f"{trade_info['type']}信号\n" \
                      f"趋势: {trade_info['trend']}\n" \
                      f"RSI: {trade_info['rsi']:.1f}\n" \
                      f"入场: {entry_price:.5f}"
        
        ax1.scatter(entry_date, entry_price, color='blue', s=200, 
                   marker='^' if trade_info['direction'] == 'long' else 'v',
                   zorder=5, edgecolors='white', linewidths=2)
        ax1.annotate(entry_reason, xy=(entry_date, entry_price),
                    xytext=(20, 40 if trade_info['direction'] == 'long' else -40),
                    textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='lightblue', alpha=0.8),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='blue'),
                    fontsize=8)
        
        # 标注止损位
        ax1.axhline(y=trade_info['stop'], color='red', linestyle='--', 
                   linewidth=1, alpha=0.6, label=f"止损: {trade_info['stop']:.5f}")
        
        # 标注离场位置
        exit_date = self.df.index[trade_info['exit_idx']]
        exit_price = trade_info['exit_price']
        exit_reason_text = f"离场: {trade_info['exit_reason']}\n" \
                          f"价格: {exit_price:.5f}\n" \
                          f"盈亏: ${trade_info['pnl']:.2f}"
        
        if trade_info['tp1_hit']:
            exit_reason_text += "\n(TP1已触发)"
        
        exit_color = 'green' if trade_info['pnl'] > 0 else 'red'
        ax1.scatter(exit_date, exit_price, color=exit_color, s=200, marker='X',
                   zorder=5, edgecolors='white', linewidths=2)
        ax1.annotate(exit_reason_text, xy=(exit_date, exit_price),
                    xytext=(20, -40 if trade_info['direction'] == 'long' else 40),
                    textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.5', 
                             facecolor='lightgreen' if trade_info['pnl'] > 0 else 'lightcoral', 
                             alpha=0.8),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color=exit_color),
                    fontsize=8)
        
        ax1.set_title(f"{self.symbol.upper()} [5分钟] - 交易#{trade_num} - {trade_info['direction'].upper()} - "
                     f"{'盈利' if trade_info['pnl'] > 0 else '亏损'} ${abs(trade_info['pnl']):.2f}",
                     fontsize=13, fontweight='bold')
        ax1.set_ylabel('价格', fontsize=10)
        ax1.legend(loc='upper left', fontsize=8)
        ax1.grid(True, alpha=0.3)
        
        # 绘制RSI
        ax2.plot(chart_data.index, chart_data['rsi'], 'purple', linewidth=1.2, label='RSI(14)')
        ax2.axhline(y=70, color='red', linestyle='--', linewidth=0.7, alpha=0.5)
        ax2.axhline(y=30, color='green', linestyle='--', linewidth=0.7, alpha=0.5)
        ax2.axhline(y=50, color='gray', linestyle='--', linewidth=0.5, alpha=0.3)
        ax2.fill_between(chart_data.index, 30, 70, alpha=0.1, color='gray')
        ax2.set_ylabel('RSI', fontsize=10)
        ax2.set_xlabel('时间', fontsize=10)
        ax2.legend(loc='upper left', fontsize=8)
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim([0, 100])
        
        # 标注入场和离场时的RSI
        ax2.scatter(entry_date, chart_data.loc[entry_date, 'rsi'], 
                   color='blue', s=80, zorder=5, edgecolors='white', linewidths=1.5)
        ax2.scatter(exit_date, chart_data.loc[exit_date, 'rsi'], 
                   color=exit_color, s=80, zorder=5, edgecolors='white', linewidths=1.5)
        
        plt.tight_layout()
        
        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.symbol}_{trade_num:03d}_{trade_info['direction']}_{timestamp}.png"
        filepath = os.path.join(self.chart_output_dir, filename)
        plt.savefig(filepath, dpi=120, bbox_inches='tight')
        plt.close()
        
        return filepath

    def run_backtest(self, signals: list, generate_charts: bool = True) -> dict:
        """
        运行回测（分批止盈策略）
        TP1(1:1) 出场50%仓位并移止损到保本，TP2(2:1) 全出
        """
        if not signals:
            return None

        capital = self.initial_capital
        trades = []
        chart_files = []

        for trade_num, signal in enumerate(signals, 1):
            idx = signal['idx']
            entry = signal['entry']
            stop = signal['stop']
            direction = signal['direction']

            risk = capital * self.risk_per_trade
            stop_distance = abs(entry - stop)
            if stop_distance == 0:
                continue

            size = risk / stop_distance

            # 5分钟周期，看后续120根K线（10小时）
            future_bars = self.df.iloc[idx:min(idx + 120, len(self.df))]

            entry_triggered = False
            entry_price = None
            exit_price = None
            exit_reason = None
            current_stop = stop
            tp1_hit = False
            tp1_size_ratio = 0.5
            entry_bar_idx = None
            exit_bar_idx = None

            for bar_idx, (i, bar) in enumerate(future_bars.iterrows()):
                current_idx = idx + bar_idx
                
                if not entry_triggered:
                    if direction == 'long' and bar['High'] >= entry:
                        entry_triggered = True
                        entry_price = min(entry, bar['High'])
                        current_stop = stop
                        entry_bar_idx = current_idx
                    elif direction == 'short' and bar['Low'] <= entry:
                        entry_triggered = True
                        entry_price = max(entry, bar['Low'])
                        current_stop = stop
                        entry_bar_idx = current_idx
                    continue

                if direction == 'long':
                    tp1_price = entry_price + stop_distance * 1.0
                    tp2_price = entry_price + stop_distance * 2.0

                    if not tp1_hit and bar['High'] >= tp1_price:
                        tp1_hit = True
                        current_stop = entry_price

                    if bar['High'] >= tp2_price:
                        exit_price = tp2_price
                        exit_reason = 'TP2'
                        exit_bar_idx = current_idx
                        break

                    if bar['Low'] <= current_stop:
                        exit_price = current_stop
                        exit_reason = 'BE' if tp1_hit else 'SL'
                        exit_bar_idx = current_idx
                        break

                else:
                    tp1_price = entry_price - stop_distance * 1.0
                    tp2_price = entry_price - stop_distance * 2.0

                    if not tp1_hit and bar['Low'] <= tp1_price:
                        tp1_hit = True
                        current_stop = entry_price

                    if bar['Low'] <= tp2_price:
                        exit_price = tp2_price
                        exit_reason = 'TP2'
                        exit_bar_idx = current_idx
                        break

                    if bar['High'] >= current_stop:
                        exit_price = current_stop
                        exit_reason = 'BE' if tp1_hit else 'SL'
                        exit_bar_idx = current_idx
                        break

                # 5分钟周期，100根K线后自动离场（约8小时）
                if bar_idx > 100:
                    exit_price = bar['Close']
                    exit_reason = 'TIME'
                    exit_bar_idx = current_idx
                    break

            if exit_price is not None and entry_price is not None:
                if direction == 'long':
                    if tp1_hit:
                        pnl = (tp1_price - entry_price) * size * tp1_size_ratio + \
                              (exit_price - entry_price) * size * (1 - tp1_size_ratio)
                    else:
                        pnl = (exit_price - entry_price) * size
                else:
                    if tp1_hit:
                        pnl = (entry_price - tp1_price) * size * tp1_size_ratio + \
                              (entry_price - exit_price) * size * (1 - tp1_size_ratio)
                    else:
                        pnl = (entry_price - exit_price) * size

                capital += pnl
                
                trade_info = {
                    'pnl': pnl,
                    'exit_reason': exit_reason,
                    'direction': direction,
                    'tp1_hit': tp1_hit,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'entry_idx': entry_bar_idx,
                    'exit_idx': exit_bar_idx,
                    'stop': stop,
                    'type': signal['type'],
                    'trend': signal.get('trend', ''),
                    'rsi': signal.get('rsi', 50)
                }
                
                trades.append(trade_info)
                
                # 只为前20笔交易生成图表（5分钟数据量大）
                if generate_charts and trade_num <= 20:
                    try:
                        chart_file = self.plot_trade_chart(trade_info, trade_num)
                        chart_files.append(chart_file)
                        print(f"  交易#{trade_num} 图表已生成: {chart_file}")
                    except Exception as e:
                        print(f"  交易#{trade_num} 图表生成失败: {e}")

        if not trades:
            return None

        df = pd.DataFrame(trades)
        wins = df[df['pnl'] > 0]
        losses = df[df['pnl'] <= 0]
        tp1_hits = df[df['tp1_hit'] == True]

        return {
            'total_trades': len(trades),
            'win_rate': len(wins) / len(df) * 100,
            'tp1_rate': len(tp1_hits) / len(df) * 100,
            'total_pnl': df['pnl'].sum(),
            'avg_win': wins['pnl'].mean() if len(wins) > 0 else 0,
            'avg_loss': abs(losses['pnl'].mean()) if len(losses) > 0 else 0,
            'profit_factor': (wins['pnl'].sum() / abs(losses['pnl'].sum()))
                             if len(losses) > 0 and losses['pnl'].sum() != 0 else 0,
            'final_capital': capital,
            'wins': len(wins),
            'losses': len(losses),
            'chart_files': chart_files
        }


def run_5min_backtest(api_key: str = None):
    """运行5分钟周期回测（Twelve Data，最近60天数据）"""

    api_key = api_key or TWELVEDATA_API_KEY

    SYMBOLS = {
        'EURUSD': 'eurusd',
        'USDJPY': 'usdjpy',
        'GBPUSD': 'gbpusd',
        'XAUUSD': 'xauusd',
        'XAGUSD': 'xagusd',
    }

    END_DATE   = datetime.now().strftime("%Y-%m-%d")
    START_DATE = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    INITIAL_CAPITAL = 100000
    RISK_PER_TRADE  = 0.02
    CHART_OUTPUT_DIR = "backtest_charts_5min"

    print("="*70)
    print("H1/H2 外汇/贵金属 5分钟周期回测 (forex_monitor_wx.py 策略)")
    print("="*70)
    print(f"数据源:   Twelve Data (免费 API, 800次/天)")
    print(f"时间周期: 5分钟")
    print(f"回测区间: {START_DATE} ~ {END_DATE} (最近60天)")
    print(f"初始资金: ${INITIAL_CAPITAL:,.0f}")
    print(f"风险比例: {RISK_PER_TRADE*100}%/笔")
    print(f"图表输出: {CHART_OUTPUT_DIR}/")
    print("策略:     双EMA排列 + RSI过滤 + H1/H2形态 + 分批止盈(TP1保本+TP2)")
    print("="*70)

    results = []

    for idx, (name, symbol) in enumerate(SYMBOLS.items()):
        print(f"\n{'='*50}")
        print(f"测试品种: {name} ({symbol})")
        print(f"{'='*50}")

        # 品种间等待1秒，避免触发API频率限制
        if idx > 0:
            time.sleep(1)

        backtester = Forex5MinBacktester(
            symbol=symbol,
            start_date=START_DATE,
            end_date=END_DATE,
            initial_capital=INITIAL_CAPITAL,
            risk_per_trade=RISK_PER_TRADE,
            chart_output_dir=CHART_OUTPUT_DIR
        )

        df = backtester.load_data_twelvedata(api_key)

        if df is None:
            print(f"  {name} 数据加载失败，跳过")
            continue

        print(f"正在寻找信号...")
        signals = backtester.find_signals()
        print(f"找到信号: {len(signals)} 个")

        if len(signals) == 0:
            print("  无信号，跳过")
            continue

        print(f"正在运行回测...")
        result = backtester.run_backtest(signals, generate_charts=True)

        if result:
            results.append({'symbol': name, **result})
            return_pct = (result['final_capital'] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
            print(f"\n--- {name} 回测结果 ---")
            print(f"交易次数:  {result['total_trades']}")
            print(f"胜率:      {result['win_rate']:.2f}%")
            print(f"TP1触及率: {result['tp1_rate']:.2f}%")
            print(f"总盈亏:    ${result['total_pnl']:,.2f}")
            print(f"平均盈利:  ${result['avg_win']:,.2f}")
            print(f"平均亏损:  ${result['avg_loss']:,.2f}")
            print(f"盈利因子:  {result['profit_factor']:.2f}")
            print(f"最终资金:  ${result['final_capital']:,.2f}")
            print(f"收益率:    {return_pct:.2f}%")
            print(f"生成图表:  {len(result['chart_files'])} 张")
        else:
            print("无有效交易")

    print("\n" + "="*80)
    print("各品种汇总对比")
    print("="*80)
    print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'TP1率':>8} "
          f"{'总盈亏':>12} {'盈利因子':>10} {'收益率':>10}")
    print("-"*85)

    for r in results:
        return_pct = (r['final_capital'] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        print(f"{r['symbol']:<10} {r['total_trades']:>8} {r['win_rate']:>7.1f}%"
              f" {r['tp1_rate']:>7.1f}% ${r['total_pnl']:>10,.0f}"
              f" {r['profit_factor']:>10.2f} {return_pct:>9.1f}%")

    if results:
        valid = [r for r in results if r['total_trades'] >= 5]
        if valid:
            best = max(valid, key=lambda x: x['win_rate'])
            print(f"\n最高胜率品种: {best['symbol']} ({best['win_rate']:.1f}%)")
        best_pnl = max(results, key=lambda x: x['total_pnl'])
        print(f"最高盈利品种: {best_pnl['symbol']} (${best_pnl['total_pnl']:,.0f})")

    total_charts = sum(len(r.get('chart_files', [])) for r in results)
    print(f"\n总共生成 {total_charts} 张交易图表，保存在 {CHART_OUTPUT_DIR}/ 目录")
    print("注意: 每个品种仅为前20笔交易生成图表")

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("H1/H2 外汇/贵金属 5分钟回测")
    print("数据源: Twelve Data (免费)")
    print("=" * 60)

    if TWELVEDATA_API_KEY == "your_api_key_here":
        print("\n[!] 请先设置 API Key！")
        print("1. 访问 https://twelvedata.com/apikey 免费注册")
        print("2. 复制 API Key")
        print("3. 将脚本顶部 TWELVEDATA_API_KEY = \"your_api_key_here\"")
        print("   替换为你的 Key，例如:")
        print("   TWELVEDATA_API_KEY = \"abc123def456...\"")
        print("\n免费账号额度: 800次/天，5个品种 × 3批次 = 15次，绰绰有余")
    else:
        try:
            results = run_5min_backtest()
        except KeyboardInterrupt:
            print("\n\n回测已手动停止 (Ctrl+C)")
        except Exception as e:
            print(f"\n回测发生错误: {e}")
            import traceback
            traceback.print_exc()
