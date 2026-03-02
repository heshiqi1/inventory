"""
H1/H2 Price Action Strategy - Forex & Metals Backtest (Improved)
外汇和贵金属日线周期回测 (数据源: Stooq, 免费无限速)
改进: 双EMA排列趋势确认 + RSI过滤 + 宽松信号 + 分批止盈
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
from datetime import datetime
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class ForexBacktester:
    """外汇回测引擎"""

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 initial_capital: float = 100000, risk_per_trade: float = 0.02,
                 chart_output_dir: str = None):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.df = None
        self.chart_output_dir = chart_output_dir or "backtest_charts"
        
        if not os.path.exists(self.chart_output_dir):
            os.makedirs(self.chart_output_dir)

    def load_data(self, timeframe: str = "1d") -> pd.DataFrame:
        """加载外汇/贵金属数据 (Stooq 数据源)"""
        print(f"正在加载 {self.symbol} ({timeframe})...")

        try:
            d1 = self.start_date.replace('-', '')
            d2 = self.end_date.replace('-', '')
            url = f"https://stooq.com/q/d/l/?s={self.symbol}&d1={d1}&d2={d2}&i=d"

            for attempt in range(3):
                try:
                    resp = requests.get(url, timeout=20,
                                        headers={'User-Agent': 'Mozilla/5.0'})
                    resp.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt < 2:
                        print(f"  连接失败，2秒后重试 ({attempt+1}/3)...")
                        time.sleep(2)
                    else:
                        raise

            self.df = pd.read_csv(io.StringIO(resp.text), parse_dates=['Date'], index_col='Date')
            self.df = self.df.sort_index()

            if len(self.df) == 0:
                print(f"  {self.symbol} 无数据")
                return None

            if isinstance(self.df.columns, pd.MultiIndex):
                self.df.columns = [col[0] for col in self.df.columns]

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

            # 成交量
            if 'Volume' in self.df.columns and self.df['Volume'].sum() > 0:
                self.df['volume_ma'] = self.df['Volume'].rolling(20).mean()
                self.df['rvol'] = self.df['Volume'] / self.df['volume_ma'].replace(0, np.nan)
                self.df['rvol'] = self.df['rvol'].fillna(1.0)
            else:
                self.df['rvol'] = 1.0

            print(f"  数据加载完成: {len(self.df)} 根K线 "
                  f"({self.df.index[0].date()} ~ {self.df.index[-1].date()})")
            return self.df

        except Exception as e:
            print(f"  加载失败: {e}")
            return None

    def detect_trend(self, idx: int) -> str:
        """
        改进: 双EMA排列趋势确认
        原: 仅EMA20斜率 > 0.001
        新: EMA20 > EMA50 排列 + 较低斜率阈值 0.0003，5根bar斜率更敏感
        """
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
        """
        改进: 宽松动量确认
        原: 连续3根趋势K线（极苛刻）
        新: 连续2根趋势K线 OR 近3根收盘价有方向性突破
        """
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
        """寻找信号"""
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
        """
        改进: 宽松多头信号 (H1/H2)
        原: body_ratio >= 0.4，扫描20根bar
        新: body_ratio >= 0.3，扫描30根bar，内层扫描15根
        """
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
        """改进: 宽松空头信号 (L1/L2)"""
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
        """绘制单个交易的K线图，标注入场、离场位置和理由"""
        idx = trade_info['entry_idx']
        start_idx = max(0, idx - 30)
        end_idx = min(len(self.df), trade_info['exit_idx'] + 10)
        
        chart_data = self.df.iloc[start_idx:end_idx].copy()
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), 
                                        gridspec_kw={'height_ratios': [3, 1]})
        
        # 绘制K线
        for i in range(len(chart_data)):
            date = chart_data.index[i]
            open_price = chart_data['Open'].iloc[i]
            high = chart_data['High'].iloc[i]
            low = chart_data['Low'].iloc[i]
            close = chart_data['Close'].iloc[i]
            
            color = 'red' if close >= open_price else 'green'
            ax1.plot([date, date], [low, high], color=color, linewidth=0.8)
            ax1.add_patch(plt.Rectangle((mdates.date2num(date) - 0.3, min(open_price, close)),
                                        0.6, abs(close - open_price),
                                        facecolor=color, edgecolor=color, alpha=0.8))
        
        # 绘制EMA
        ax1.plot(chart_data.index, chart_data['ema_20'], 'b-', linewidth=1.5, 
                label='EMA20', alpha=0.7)
        ax1.plot(chart_data.index, chart_data['ema_50'], 'orange', linewidth=1.5,
                label='EMA50', alpha=0.7)
        
        # 标注入场位置
        entry_date = self.df.index[trade_info['entry_idx']]
        entry_price = trade_info['entry_price']
        
        entry_reason = f"{trade_info['type']}信号\n" \
                      f"趋势: {trade_info['trend']}\n" \
                      f"RSI: {trade_info['rsi']:.1f}\n" \
                      f"入场: {entry_price:.5f}"
        
        ax1.scatter(entry_date, entry_price, color='blue', s=200, marker='^' if trade_info['direction'] == 'long' else 'v',
                   zorder=5, edgecolors='white', linewidths=2)
        ax1.annotate(entry_reason, xy=(entry_date, entry_price),
                    xytext=(20, 40 if trade_info['direction'] == 'long' else -40),
                    textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='lightblue', alpha=0.8),
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0', color='blue'),
                    fontsize=9)
        
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
                    fontsize=9)
        
        ax1.set_title(f"{self.symbol.upper()} - 交易#{trade_num} - {trade_info['direction'].upper()} - "
                     f"{'盈利' if trade_info['pnl'] > 0 else '亏损'} ${abs(trade_info['pnl']):.2f}",
                     fontsize=14, fontweight='bold')
        ax1.set_ylabel('价格', fontsize=11)
        ax1.legend(loc='upper left', fontsize=9)
        ax1.grid(True, alpha=0.3)
        
        # 绘制RSI
        ax2.plot(chart_data.index, chart_data['rsi'], 'purple', linewidth=1.5, label='RSI(14)')
        ax2.axhline(y=70, color='red', linestyle='--', linewidth=0.8, alpha=0.5)
        ax2.axhline(y=30, color='green', linestyle='--', linewidth=0.8, alpha=0.5)
        ax2.axhline(y=50, color='gray', linestyle='--', linewidth=0.5, alpha=0.3)
        ax2.fill_between(chart_data.index, 30, 70, alpha=0.1, color='gray')
        ax2.set_ylabel('RSI', fontsize=11)
        ax2.set_xlabel('日期', fontsize=11)
        ax2.legend(loc='upper left', fontsize=9)
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim([0, 100])
        
        # 标注入场和离场时的RSI
        ax2.scatter(entry_date, chart_data.loc[entry_date, 'rsi'], 
                   color='blue', s=100, zorder=5, edgecolors='white', linewidths=1.5)
        ax2.scatter(exit_date, chart_data.loc[exit_date, 'rsi'], 
                   color=exit_color, s=100, zorder=5, edgecolors='white', linewidths=1.5)
        
        plt.tight_layout()
        
        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.symbol}_{trade_num:03d}_{trade_info['direction']}_{timestamp}.png"
        filepath = os.path.join(self.chart_output_dir, filename)
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        
        return filepath

    def run_backtest(self, signals: list, generate_charts: bool = True) -> dict:
        """
        改进: 分批止盈 + 移保本 + 生成交易图表
        原: 单一TP 2:1
        新: TP1(1:1) 出场50%仓位并移止损到保本，TP2(2:1) 全出
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

            future_bars = self.df.iloc[idx:min(idx + 30, len(self.df))]

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

                if bar_idx > 20:
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
                
                if generate_charts:
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


def run_forex_backtest():
    """运行外汇回测"""

    SYMBOLS = {
        'EURUSD': 'eurusd',
        'USDJPY': 'usdjpy',
        'GBPUSD': 'gbpusd',
        'XAUUSD': 'xauusd',
        'XAGUSD': 'xagusd',
    }

    START_DATE = "2022-01-01"
    END_DATE = "2025-01-01"
    TIMEFRAME = "1d"
    INITIAL_CAPITAL = 100000
    RISK_PER_TRADE = 0.02
    CHART_OUTPUT_DIR = "backtest_charts"

    print("="*60)
    print("H1/H2 外汇/贵金属 日线周期回测 (Stooq 数据源) — 优化版")
    print("="*60)
    print(f"时间周期: {TIMEFRAME}")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: ${INITIAL_CAPITAL:,.0f}")
    print(f"风险比例: {RISK_PER_TRADE*100}%/笔")
    print(f"图表输出目录: {CHART_OUTPUT_DIR}")
    print("改进项: 双EMA排列 + RSI过滤 + 宽松信号 + 分批止盈(TP1保本+TP2)")
    print("="*60)

    results = []

    for name, symbol in SYMBOLS.items():
        print(f"\n{'='*40}")
        print(f"测试品种: {name} ({symbol})")
        print(f"{'='*40}")

        backtester = ForexBacktester(
            symbol=symbol,
            start_date=START_DATE,
            end_date=END_DATE,
            initial_capital=INITIAL_CAPITAL,
            risk_per_trade=RISK_PER_TRADE,
            chart_output_dir=CHART_OUTPUT_DIR
        )

        df = backtester.load_data(timeframe=TIMEFRAME)

        if df is None:
            continue

        signals = backtester.find_signals()
        print(f"找到信号: {len(signals)} 个")

        result = backtester.run_backtest(signals, generate_charts=True)

        if result:
            results.append({'symbol': name, **result})

            print(f"\n--- {name} 回测结果 ---")
            print(f"交易次数: {result['total_trades']}")
            print(f"胜率:     {result['win_rate']:.2f}%")
            print(f"TP1触及率: {result['tp1_rate']:.2f}%")
            print(f"总盈亏:   ${result['total_pnl']:,.2f}")
            print(f"平均盈利: ${result['avg_win']:,.2f}")
            print(f"平均亏损: ${result['avg_loss']:,.2f}")
            print(f"盈利因子: {result['profit_factor']:.2f}")
            print(f"最终资金: ${result['final_capital']:,.2f}")
            print(f"生成图表: {len(result['chart_files'])} 张")
        else:
            print("无有效交易")

    print("\n" + "="*60)
    print("各品种汇总对比")
    print("="*60)
    print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'TP1率':>8} {'总盈亏':>12} {'盈利因子':>10}")
    print("-"*65)

    for r in results:
        print(f"{r['symbol']:<10} {r['total_trades']:>8} {r['win_rate']:>7.1f}%"
              f" {r['tp1_rate']:>7.1f}% ${r['total_pnl']:>10,.0f} {r['profit_factor']:>10.2f}")

    if results:
        valid = [r for r in results if r['total_trades'] >= 5]
        if valid:
            best = max(valid, key=lambda x: x['win_rate'])
            print(f"\n最高胜率品种: {best['symbol']} ({best['win_rate']:.1f}%)")
        best_pnl = max(results, key=lambda x: x['total_pnl'])
        print(f"最高盈利品种: {best_pnl['symbol']} (${best_pnl['total_pnl']:,.0f})")

    total_charts = sum(len(r.get('chart_files', [])) for r in results)
    print(f"\n总共生成 {total_charts} 张交易图表，保存在 {CHART_OUTPUT_DIR} 目录")

    return results


if __name__ == "__main__":
    results = run_forex_backtest()
