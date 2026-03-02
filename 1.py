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
warnings.filterwarnings('ignore')


class ForexBacktester:
    """外汇回测引擎"""

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 initial_capital: float = 100000, risk_per_trade: float = 0.02):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.df = None

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
                except requests.RequestException as e:
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

            # ★ 改进1: 新增 RSI(14) 过滤器
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
        ★ 改进2: 宽松趋势检测
        原: 只用EMA20斜率 0.001
        新: 双EMA排列 + 较低斜率阈值 0.0003
        """
        if idx < 50:
            return None

        ema_20 = self.df['ema_20'].iloc[idx]
        ema_50 = self.df['ema_50'].iloc[idx]
        close_price = self.df['Close'].iloc[idx]
        # 用5根bar斜率代替10根，更敏感
        ema_slope = (self.df['ema_20'].iloc[idx] - self.df['ema_20'].iloc[idx-5]) / self.df['ema_20'].iloc[idx-5]

        # 双EMA排列确认趋势
        if close_price > ema_20 and ema_20 > ema_50 and ema_slope > 0.0003:
            return 'uptrend'
        elif close_price < ema_20 and ema_20 < ema_50 and ema_slope < -0.0003:
            return 'downtrend'

        return None

    def is_momentum_confirmed(self, idx: int, direction: str) -> bool:
        """
        ★ 改进3: 用动量确认替代严格的连续趋势K线检测
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

        # 条件A: 连续2根趋势K线（宽松版）
        if len(directions) >= 2:
            if directions[-1] == directions[-2] and trend_bars[-1] and trend_bars[-2]:
                return True

        # 条件B: 近3根bar收盘价有方向性动量
        closes = self.df['Close'].iloc[idx-3:idx].values
        if direction == 'uptrend' and closes[-1] > closes[0]:
            return True
        if direction == 'downtrend' and closes[-1] < closes[0]:
            return True

        return False

    def find_signals(self) -> list:
        """寻找信号"""
        signals = []
        last_signal_idx = -10  # 防止信号扎堆，至少间隔5根bar

        for idx in range(50, len(self.df) - 10):
            trend = self.detect_trend(idx)

            if trend is None:
                continue

            # ★ 用新的动量确认替代原来的强突破检测
            if not self.is_momentum_confirmed(idx, trend):
                continue

            # ★ 改进4: RSI过滤 — 避免追高杀低
            rsi = self.df['rsi'].iloc[idx]
            if trend == 'uptrend' and rsi > 70:
                continue  # 超买，不做多
            if trend == 'downtrend' and rsi < 30:
                continue  # 超卖，不做空

            if idx - last_signal_idx < 5:
                continue  # 信号太密集，跳过

            if trend == 'uptrend':
                signal = self.check_bull_signal(idx)
            else:
                signal = self.check_bear_signal(idx)

            if signal:
                signals.append(signal)
                last_signal_idx = signal['idx']

        return signals

    def check_bull_signal(self, idx: int) -> dict:
        """
        ★ 改进5: 宽松多头信号
        原: body_ratio >= 0.4
        新: body_ratio >= 0.3，扫描范围扩大
        """
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if self.df['High'].iloc[i] < self.df['High'].iloc[i+1]:
                for j in range(i, min(i + 15, len(self.df) - 1)):
                    if self.df['High'].iloc[j] > self.df['High'].iloc[j-1]:
                        if self.df['Close'].iloc[j] > self.df['Open'].iloc[j]:
                            if self.df['body_ratio'].iloc[j] >= 0.3:  # 宽松
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
        """★ 改进5: 宽松空头信号"""
        for i in range(idx - 1, max(idx - 30, 5), -1):
            if self.df['Low'].iloc[i] > self.df['Low'].iloc[i+1]:
                for j in range(i, min(i + 15, len(self.df) - 1)):
                    if self.df['Low'].iloc[j] < self.df['Low'].iloc[j-1]:
                        if self.df['Close'].iloc[j] < self.df['Open'].iloc[j]:
                            if self.df['body_ratio'].iloc[j] >= 0.3:  # 宽松
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

    def run_backtest(self, signals: list) -> dict:
        """
        ★ 改进6: 分批止盈 — TP1在1:1锁利+移保本，TP2在2:1
        原: 只有单一2:1止盈
        新: TP1(1:1) + 移止损到保本 + TP2(2:1)
        """
        if not signals:
            return None

        capital = self.initial_capital
        trades = []

        for signal in signals:
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
            current_stop = stop        # 动态止损（移到保本后会更新）
            tp1_hit = False            # TP1是否已触发
            tp1_size_ratio = 0.5       # TP1出场50%仓位

            for bar_idx, (i, bar) in enumerate(future_bars.iterrows()):
                if not entry_triggered:
                    if direction == 'long' and bar['High'] >= entry:
                        entry_triggered = True
                        entry_price = min(entry, bar['High'])
                        current_stop = stop
                    elif direction == 'short' and bar['Low'] <= entry:
                        entry_triggered = True
                        entry_price = max(entry, bar['Low'])
                        current_stop = stop
                    continue

                if direction == 'long':
                    tp1_price = entry_price + stop_distance * 1.0  # 1:1
                    tp2_price = entry_price + stop_distance * 2.0  # 2:1

                    # TP1: 1:1 止盈 → 移止损到保本
                    if not tp1_hit and bar['High'] >= tp1_price:
                        tp1_hit = True
                        current_stop = entry_price  # 移到保本

                    # TP2: 2:1 全部出场
                    if bar['High'] >= tp2_price:
                        exit_price = tp2_price
                        exit_reason = 'TP2'
                        break

                    # 止损
                    if bar['Low'] <= current_stop:
                        exit_price = current_stop
                        exit_reason = 'BE' if tp1_hit else 'SL'
                        break

                else:  # short
                    tp1_price = entry_price - stop_distance * 1.0
                    tp2_price = entry_price - stop_distance * 2.0

                    if not tp1_hit and bar['Low'] <= tp1_price:
                        tp1_hit = True
                        current_stop = entry_price

                    if bar['Low'] <= tp2_price:
                        exit_price = tp2_price
                        exit_reason = 'TP2'
                        break

                    if bar['High'] >= current_stop:
                        exit_price = current_stop
                        exit_reason = 'BE' if tp1_hit else 'SL'
                        break

                # 时间止损
                if bar_idx > 20:
                    exit_price = bar['Close']
                    exit_reason = 'TIME'
                    break

            if exit_price is not None and entry_price is not None:
                if direction == 'long':
                    # 如果TP1已触发：50%仓位在1:1出场，50%在exit_price出场
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
                trades.append({
                    'pnl': pnl,
                    'exit_reason': exit_reason,
                    'direction': direction,
                    'tp1_hit': tp1_hit
                })

        if not trades:
            return None

        df = pd.DataFrame(trades)
        wins = df[df['pnl'] > 0]
        losses = df[df['pnl'] <= 0]

        return {
            'total_trades': len(trades),
            'win_rate': len(wins) / len(df) * 100,
            'total_pnl': df['pnl'].sum(),
            'avg_win': wins['pnl'].mean() if len(wins) > 0 else 0,
            'avg_loss': abs(losses['pnl'].mean()) if len(losses) > 0 else 0,
            'profit_factor': (wins['pnl'].sum() / abs(losses['pnl'].sum()))
                             if len(losses) > 0 and losses['pnl'].sum() != 0 else 0,
            'final_capital': capital,
            'wins': len(wins),
            'losses': len(losses)
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

    # ★ 改进7: 扩大回测区间到3年，增加样本量
    START_DATE = "2018-01-01"
    END_DATE = "2021-01-01"
    TIMEFRAME = "1d"
    INITIAL_CAPITAL = 1000
    RISK_PER_TRADE = 0.02

    print("="*60)
    print("H1/H2 外汇/贵金属 日线周期回测 (Stooq 数据源) — 优化版")
    print("="*60)
    print(f"时间周期: {TIMEFRAME}")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: ${INITIAL_CAPITAL:,.0f}")
    print(f"风险比例: {RISK_PER_TRADE*100}%/笔")
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
            risk_per_trade=RISK_PER_TRADE
        )

        df = backtester.load_data(timeframe=TIMEFRAME)

        if df is None:
            continue

        signals = backtester.find_signals()
        print(f"找到信号: {len(signals)} 个")

        result = backtester.run_backtest(signals)

        if result:
            results.append({'symbol': name, **result})

            print(f"\n--- {name} 回测结果 ---")
            print(f"交易次数: {result['total_trades']}")
            print(f"胜率: {result['win_rate']:.2f}%")
            print(f"总盈亏: ${result['total_pnl']:,.2f}")
            print(f"平均盈利: ${result['avg_win']:,.2f}")
            print(f"平均亏损: ${result['avg_loss']:,.2f}")
            print(f"盈利因子: {result['profit_factor']:.2f}")
            print(f"最终资金: ${result['final_capital']:,.2f}")
        else:
            print("无有效交易")

    print("\n" + "="*60)
    print("各品种汇总对比")
    print("="*60)
    print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'总盈亏':>12} {'盈利因子':>10}")
    print("-"*60)

    for r in results:
        print(f"{r['symbol']:<10} {r['total_trades']:>8} {r['win_rate']:>7.1f}%"
              f" ${r['total_pnl']:>10,.0f} {r['profit_factor']:>10.2f}")

    if results:
        best = max(results, key=lambda x: x['win_rate'] if x['total_trades'] >= 5 else 0)
        print(f"\n最高胜率品种: {best['symbol']} ({best['win_rate']:.1f}%)")
        best_pnl = max(results, key=lambda x: x['total_pnl'])
        print(f"最高盈利品种: {best_pnl['symbol']} (${best_pnl['total_pnl']:,.0f})")

    return results


if __name__ == "__main__":
    results = run_forex_backtest()
