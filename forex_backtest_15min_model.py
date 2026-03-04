"""
15分钟外汇短线波段策略回测

核心规则链路:
1) 1h + 15m 多周期趋势同向 (EMA20/50/100 + 斜率)
2) 前20根K线高低点突破 + 1~2根趋势K跟随
3) 回调到EMA20附近
4) 反转K线/强趋势K触发入场
5) 结构止损优先, ATR兜底, 分批止盈(TP1 + TP2)
6) 样本内外 + walk-forward 验证
"""

from __future__ import annotations

import os
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "61141e293ece4cad906e65413921b012")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


@dataclass
class StrategyConfig:
    risk_per_trade: float = 0.02
    lookback_breakout: int = 20
    trendbar_body_vs_wick: float = 2.0
    doji_body_vs_wick: float = 0.5
    ema_retest_tolerance: float = 0.0015
    pullback_timeout_1h_bars: int = 20
    follow_bars: int = 2
    single_overlap_limit: float = 0.3
    single_lower_wick_ratio: float = 0.5
    single_upper_wick_ratio: float = 0.1
    double_body_diff_limit: float = 0.2
    atr_stop_multiplier: float = 2.0
    tp1_split_ratio: float = 0.5
    tp2_rr: float = 2.0
    max_holding_bars_15m: int = 160
    min_gap_between_trades: int = 5
    min_stop_atr_ratio: float = 0.5
    max_stop_atr_multiple: float = 2.0
    max_leverage_per_trade: float = 20.0
    max_abs_rr_multiple: float = 10.0


class Forex15MinBacktester:
    """15分钟多周期价格行为回测器。"""

    def __init__(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
        config: StrategyConfig | None = None,
    ):
        self.symbol = symbol.upper()
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.config = config or StrategyConfig()
        self.df_15m: pd.DataFrame | None = None
        self.df_1h: pd.DataFrame | None = None
        self.rule_audit = self._build_rule_audit_table()

    # --------------------------
    # 待办1: 规则体检与参数表
    # --------------------------
    def _build_rule_audit_table(self) -> dict:
        """
        将策略文字规则转换为可编程判定项，并给出默认值与歧义说明。
        """
        return {
            "rule_table": [
                {
                    "module": "trend_filter",
                    "rule": "1h与15m同向EMA20>EMA50>EMA100(或反向)且斜率同向",
                    "params": {"ema_set": [20, 50, 100], "slope_lag": 1},
                },
                {
                    "module": "breakout",
                    "rule": "当前K突破前20根高/低点，且为趋势K",
                    "params": {
                        "lookback_breakout": self.config.lookback_breakout,
                        "trendbar_body_vs_wick": self.config.trendbar_body_vs_wick,
                    },
                },
                {
                    "module": "follow_quality",
                    "rule": "突破后1~2根内至少1根同向趋势K",
                    "params": {"follow_bars": self.config.follow_bars},
                },
                {
                    "module": "pullback",
                    "rule": "不再创新高/新低后回踩EMA20，超时放弃",
                    "params": {
                        "ema_retest_tolerance": self.config.ema_retest_tolerance,
                        "pullback_timeout_1h_bars": self.config.pullback_timeout_1h_bars,
                    },
                },
                {
                    "module": "entry_trigger",
                    "rule": "单K/双K反转优先，强趋势K作为替代",
                    "params": {
                        "single_overlap_limit": self.config.single_overlap_limit,
                        "double_body_diff_limit": self.config.double_body_diff_limit,
                    },
                },
                {
                    "module": "risk_exit",
                    "rule": "结构止损优先，ATR兜底，TP1/TP2分批",
                    "params": {
                        "risk_per_trade": self.config.risk_per_trade,
                        "atr_stop_multiplier": self.config.atr_stop_multiplier,
                        "tp2_rr": self.config.tp2_rr,
                    },
                },
            ],
            "ambiguities_and_defaults": [
                "双K反转中“原趋势”存在歧义，默认解释为“回调方向后再转回交易方向”。",
                "突破后跟随K线质量默认“1~2根内至少1根同向趋势K”；可切换为“2根都满足”。",
                "显著点使用左右20根确认，实盘为避免未来函数，仅使用已确认(至少滞后20根)的拐点。",
                "未实现箱体/楔形/旗形/A-B-C几何识别，预留插件接口，当前先走主链路。",
            ],
        }

    def print_rule_audit(self) -> None:
        print("\n[规则体检] 可编程判定表")
        for row in self.rule_audit["rule_table"]:
            print(f"- {row['module']}: {row['rule']} | params={row['params']}")
        print("[规则体检] 关键歧义与默认解释")
        for item in self.rule_audit["ambiguities_and_defaults"]:
            print(f"- {item}")

    # --------------------------
    # 数据与指标
    # --------------------------
    @staticmethod
    def _symbol_to_twelvedata(symbol: str) -> str | None:
        m = {
            "EURUSD": "EUR/USD",
            "USDJPY": "USD/JPY",
            "AUDUSD": "AUD/USD",
            "XAUUSD": "XAU/USD",
            "XAGUSD": "XAG/USD",
        }
        return m.get(symbol.upper())

    def load_data_twelvedata(self, api_key: str, batches: int = 6) -> pd.DataFrame | None:
        td_symbol = self._symbol_to_twelvedata(self.symbol)
        if not td_symbol:
            print(f"[{self.symbol}] 不支持的品种")
            return None
        if not api_key:
            print("未检测到 TWELVEDATA_API_KEY，请先配置环境变量或在调用时传入。")
            return None

        print(f"[{self.symbol}] 正在加载15分钟数据: {td_symbol}")
        base_url = "https://api.twelvedata.com/time_series"
        all_frames: list[pd.DataFrame] = []
        end_dt = None

        for batch in range(batches):
            params = {
                "symbol": td_symbol,
                "interval": "15min",
                "outputsize": 5000,
                "apikey": api_key,
                "format": "JSON",
                "timezone": "UTC",
                "order": "DESC",
            }
            if end_dt:
                params["end_date"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")

            data = self._request_twelvedata_with_retry(base_url, params, batch + 1)
            if data is None:
                return None

            values = data.get("values", [])
            if not values:
                break

            batch_df = pd.DataFrame(values)
            batch_df["datetime"] = pd.to_datetime(batch_df["datetime"])
            batch_df = batch_df.set_index("datetime").rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            for col in ["Open", "High", "Low", "Close"]:
                batch_df[col] = pd.to_numeric(batch_df[col], errors="coerce")

            all_frames.append(batch_df[["Open", "High", "Low", "Close"]])
            oldest = batch_df.index.min()
            end_dt = oldest.to_pydatetime() - timedelta(minutes=1)
            time.sleep(1)

        if not all_frames:
            print(f"[{self.symbol}] 无数据")
            return None

        combined = pd.concat(all_frames)
        combined = combined[~combined.index.duplicated(keep="first")].sort_index()

        start_ts = pd.Timestamp(self.start_date)
        end_ts = pd.Timestamp(self.end_date) + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
        if combined.index.tz is not None:
            start_ts = start_ts.tz_localize(combined.index.tz)
            end_ts = end_ts.tz_localize(combined.index.tz)
        combined = combined[(combined.index >= start_ts) & (combined.index <= end_ts)]

        self.df_15m = combined.copy()
        self._calc_indicators()
        print(f"[{self.symbol}] 数据完成: {len(self.df_15m)} 根15m K线")
        return self.df_15m

    def _request_twelvedata_with_retry(self, base_url: str, params: dict, batch_no: int) -> dict | None:
        """处理分钟限流和短时网络异常的自动重试。"""
        max_retry = 6
        for attempt in range(max_retry):
            try:
                resp = requests.get(base_url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                if attempt == max_retry - 1:
                    print(f"[{self.symbol}] 第{batch_no}批请求失败: {e}")
                    return None
                wait_s = min(5 * (attempt + 1), 20)
                print(f"[{self.symbol}] 网络异常，{wait_s}s后重试: {e}")
                time.sleep(wait_s)
                continue

            if data.get("status") != "error":
                return data

            msg = str(data.get("message", ""))
            lower_msg = msg.lower()
            if "run out of api credits for the current minute" in lower_msg or "wait for the next minute" in lower_msg:
                now = datetime.now()
                wait_s = (60 - now.second) + 2
                print(f"[{self.symbol}] 触发分钟限流，第{batch_no}批等待 {wait_s}s 后重试")
                time.sleep(wait_s)
                continue

            print(f"[{self.symbol}] API错误: {msg}")
            return None

        print(f"[{self.symbol}] 第{batch_no}批重试后仍失败")
        return None

    def _calc_indicators(self) -> None:
        if self.df_15m is None or self.df_15m.empty:
            return

        df = self.df_15m
        for span in [20, 50, 100]:
            df[f"ema_{span}"] = df["Close"].ewm(span=span, adjust=False).mean()

        df["range"] = df["High"] - df["Low"]
        df["body"] = (df["Close"] - df["Open"]).abs()
        df["upper_wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
        df["lower_wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]
        df["wick_sum"] = df["upper_wick"] + df["lower_wick"]
        df["direction"] = np.where(df["Close"] >= df["Open"], 1, -1)

        ratio_trend = df["body"] / df["wick_sum"].replace(0, np.nan)
        ratio_doji = df["body"] / df["wick_sum"].replace(0, np.nan)
        df["is_trend_bar"] = ratio_trend > self.config.trendbar_body_vs_wick
        df["is_doji_like"] = ratio_doji <= self.config.doji_body_vs_wick
        df["atr14"] = df["range"].rolling(14).mean()

        # 前20根突破基准 (shift避免未来函数)
        look = self.config.lookback_breakout
        df["prior20_high"] = df["High"].shift(1).rolling(look).max()
        df["prior20_low"] = df["Low"].shift(1).rolling(look).min()

        # 1h重采样指标，用于多周期过滤
        df_1h = (
            df[["Open", "High", "Low", "Close"]]
            .resample("1h")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
            .dropna()
        )
        for span in [20, 50, 100]:
            df_1h[f"ema_{span}"] = df_1h["Close"].ewm(span=span, adjust=False).mean()
            df_1h[f"ema_{span}_slope"] = df_1h[f"ema_{span}"] - df_1h[f"ema_{span}"].shift(1)

        self.df_1h = df_1h
        align = df_1h[
            [
                "ema_20",
                "ema_50",
                "ema_100",
                "ema_20_slope",
                "ema_50_slope",
                "ema_100_slope",
            ]
        ].reindex(df.index, method="ffill")
        for c in align.columns:
            df[f"{c}_1h"] = align[c]

        # 显著点(左右20)并确保仅用“已确认”拐点
        window = 41
        c_high = df["High"].rolling(window=window, center=True).max()
        c_low = df["Low"].rolling(window=window, center=True).min()
        df["pivot_high_raw"] = df["High"] == c_high
        df["pivot_low_raw"] = df["Low"] == c_low

        # pivot确认需要右侧20根，故确认时点在pivot发生后20根
        df["pivot_high_confirmed"] = df["pivot_high_raw"].shift(20).fillna(False)
        df["pivot_low_confirmed"] = df["pivot_low_raw"].shift(20).fillna(False)

    # --------------------------
    # 主链路判定
    # --------------------------
    def _trend_direction(self, i: int) -> str | None:
        row = self.df_15m.iloc[i]
        prev = self.df_15m.iloc[i - 1]

        bull_15 = (
            row["ema_20"] > row["ema_50"] > row["ema_100"]
            and row["ema_20"] > prev["ema_20"]
            and row["ema_50"] > prev["ema_50"]
            and row["ema_100"] > prev["ema_100"]
        )
        bear_15 = (
            row["ema_20"] < row["ema_50"] < row["ema_100"]
            and row["ema_20"] < prev["ema_20"]
            and row["ema_50"] < prev["ema_50"]
            and row["ema_100"] < prev["ema_100"]
        )

        bull_1h = (
            row["ema_20_1h"] > row["ema_50_1h"] > row["ema_100_1h"]
            and row["ema_20_slope_1h"] > 0
            and row["ema_50_slope_1h"] > 0
            and row["ema_100_slope_1h"] > 0
        )
        bear_1h = (
            row["ema_20_1h"] < row["ema_50_1h"] < row["ema_100_1h"]
            and row["ema_20_slope_1h"] < 0
            and row["ema_50_slope_1h"] < 0
            and row["ema_100_slope_1h"] < 0
        )

        if bull_15 and bull_1h:
            return "long"
        if bear_15 and bear_1h:
            return "short"
        return None

    def _is_breakout(self, i: int, direction: str) -> bool:
        row = self.df_15m.iloc[i]
        if direction == "long":
            return (
                row["High"] > row["prior20_high"]
                and row["Close"] > row["Open"]
                and row["is_trend_bar"]
            )
        return (
            row["Low"] < row["prior20_low"]
            and row["Close"] < row["Open"]
            and row["is_trend_bar"]
        )

    def _follow_quality_ok(self, breakout_i: int, direction: str) -> bool:
        end_i = min(len(self.df_15m) - 1, breakout_i + self.config.follow_bars)
        if end_i <= breakout_i:
            return False
        follow = self.df_15m.iloc[breakout_i + 1 : end_i + 1]
        if follow.empty:
            return False

        if direction == "long":
            cond = (follow["direction"] > 0) & (follow["is_trend_bar"])
        else:
            cond = (follow["direction"] < 0) & (follow["is_trend_bar"])
        return cond.any()

    def _retest_ema20_zone(self, i: int, direction: str) -> bool:
        row = self.df_15m.iloc[i]
        ema20 = row["ema_20"]
        tol = self.config.ema_retest_tolerance

        near = abs(row["Close"] - ema20) / max(abs(ema20), 1e-9) <= tol or (
            row["Low"] <= ema20 <= row["High"]
        )
        if not near:
            return False
        if direction == "long":
            return row["Close"] >= ema20 * (1 - tol)
        return row["Close"] <= ema20 * (1 + tol)

    def _single_reversal(self, i: int, direction: str) -> bool:
        """
        单K反转 + 下一根强势跟随。
        """
        if i + 1 >= len(self.df_15m):
            return False
        cur = self.df_15m.iloc[i]
        prev = self.df_15m.iloc[i - 1]
        nxt = self.df_15m.iloc[i + 1]

        prev_body = abs(prev["Close"] - prev["Open"])
        if prev_body <= 1e-12 or cur["range"] <= 1e-12:
            return False

        overlap = max(0.0, min(cur["Close"], prev["Close"]) - max(cur["Open"], prev["Open"]))
        overlap_ratio = overlap / prev_body
        lower_ratio = cur["lower_wick"] / cur["range"]
        upper_ratio = cur["upper_wick"] / cur["range"]

        if direction == "long":
            shape_ok = (
                overlap_ratio < self.config.single_overlap_limit
                and lower_ratio >= self.config.single_lower_wick_ratio
                and upper_ratio < self.config.single_upper_wick_ratio
            )
            follow_ok = nxt["direction"] > 0 and nxt["is_trend_bar"]
            return bool(shape_ok and follow_ok)

        shape_ok = (
            overlap_ratio < self.config.single_overlap_limit
            and upper_ratio >= self.config.single_lower_wick_ratio
            and lower_ratio < self.config.single_upper_wick_ratio
        )
        follow_ok = nxt["direction"] < 0 and nxt["is_trend_bar"]
        return bool(shape_ok and follow_ok)

    def _double_reversal(self, i: int, direction: str) -> bool:
        """
        双K反转默认解释:
        - 第一根沿回调方向(与目标交易方向相反)
        - 第二根回到目标交易方向
        """
        if i + 1 >= len(self.df_15m):
            return False
        a = self.df_15m.iloc[i]
        b = self.df_15m.iloc[i + 1]
        if not (a["is_trend_bar"] and b["is_trend_bar"]):
            return False
        body_a = abs(a["Close"] - a["Open"])
        body_b = abs(b["Close"] - b["Open"])
        if max(body_a, body_b) <= 1e-12:
            return False
        diff_ratio = abs(body_a - body_b) / max(body_a, body_b)
        if diff_ratio > self.config.double_body_diff_limit:
            return False

        if direction == "long":
            return a["direction"] < 0 and b["direction"] > 0
        return a["direction"] > 0 and b["direction"] < 0

    def _strong_trendbar_entry(self, i: int, direction: str) -> bool:
        row = self.df_15m.iloc[i]
        if direction == "long":
            return row["direction"] > 0 and row["is_trend_bar"]
        return row["direction"] < 0 and row["is_trend_bar"]

    def _last_confirmed_pivot(self, i: int, direction: str) -> float | None:
        hist = self.df_15m.iloc[: i + 1]
        if direction == "long":
            piv = hist[hist["pivot_low_confirmed"]]
            if len(piv) == 0:
                return None
            return float(piv["Low"].iloc[-1])
        piv = hist[hist["pivot_high_confirmed"]]
        if len(piv) == 0:
            return None
        return float(piv["High"].iloc[-1])

    def _target_pivot(self, i: int, direction: str) -> float | None:
        hist = self.df_15m.iloc[: i + 1]
        if direction == "long":
            piv = hist[hist["pivot_high_confirmed"]]
            if len(piv) == 0:
                return None
            return float(piv["High"].iloc[-1])
        piv = hist[hist["pivot_low_confirmed"]]
        if len(piv) == 0:
            return None
        return float(piv["Low"].iloc[-1])

    def _pip_size(self) -> float:
        if self.symbol.endswith("JPY"):
            return 0.01
        if self.symbol in {"XAUUSD", "XAGUSD"}:
            return 0.01
        return 0.0001

    def _spread_slippage_price_cost(self) -> float:
        # 简化成本模型(点差+滑点, 单边)
        spread_map = {
            "EURUSD": 1.2,
            "USDJPY": 1.5,
            "AUDUSD": 1.4,
            "XAUUSD": 25.0,
            "XAGUSD": 20.0,
        }
        slip_map = {
            "EURUSD": 0.3,
            "USDJPY": 0.4,
            "AUDUSD": 0.3,
            "XAUUSD": 5.0,
            "XAGUSD": 4.0,
        }
        pip = self._pip_size()
        spread = spread_map.get(self.symbol, 1.5)
        slip = slip_map.get(self.symbol, 0.3)
        return (spread * 0.5 + slip) * pip

    def _spread_price(self) -> float:
        spread_map = {
            "EURUSD": 1.2,
            "USDJPY": 1.5,
            "AUDUSD": 1.4,
            "XAUUSD": 25.0,
            "XAGUSD": 20.0,
        }
        return spread_map.get(self.symbol, 1.5) * self._pip_size()

    def find_setups(self, df: pd.DataFrame | None = None) -> list[dict]:
        """
        待办2: 多周期趋势->突破->回调->入场触发 主链路信号。
        """
        if df is None:
            df = self.df_15m
        if df is None or len(df) < 300:
            return []

        original_df = self.df_15m
        self.df_15m = df

        setups: list[dict] = []
        i = 120
        timeout_15m = self.config.pullback_timeout_1h_bars * 4
        last_signal_i = -9999

        while i < len(self.df_15m) - 3:
            if i - last_signal_i < self.config.min_gap_between_trades:
                i += 1
                continue

            direction = self._trend_direction(i)
            if direction is None:
                i += 1
                continue

            if not self._is_breakout(i, direction):
                i += 1
                continue

            if not self._follow_quality_ok(i, direction):
                i += 1
                continue

            breakout_i = i
            breakout_extreme = (
                self.df_15m.iloc[i]["High"] if direction == "long" else self.df_15m.iloc[i]["Low"]
            )
            expiry_i = min(len(self.df_15m) - 3, breakout_i + timeout_15m)

            j = breakout_i + 1
            found = False
            while j <= expiry_i:
                row = self.df_15m.iloc[j]
                if direction == "long":
                    breakout_extreme = max(breakout_extreme, row["High"])
                    in_pullback = row["High"] < breakout_extreme
                else:
                    breakout_extreme = min(breakout_extreme, row["Low"])
                    in_pullback = row["Low"] > breakout_extreme

                if in_pullback and self._retest_ema20_zone(j, direction):
                    trigger = (
                        self._single_reversal(j, direction)
                        or self._double_reversal(j, direction)
                        or self._strong_trendbar_entry(j, direction)
                    )
                    if trigger:
                        entry_i = j + 1
                        setups.append(
                            {
                                "signal_i": j,
                                "entry_i": entry_i,
                                "direction": direction,
                                "breakout_i": breakout_i,
                                "reason": "breakout_pullback_trigger",
                            }
                        )
                        last_signal_i = j
                        i = entry_i + 1
                        found = True
                        break
                j += 1

            if not found:
                i += 1

        self.df_15m = original_df
        return setups

    # --------------------------
    # 待办3: 风控与出场
    # --------------------------
    def run_backtest(
        self,
        setups: list[dict],
        df: pd.DataFrame | None = None,
        max_closed_trades: int | None = None,
    ) -> dict | None:
        if df is None:
            df = self.df_15m
        if df is None:
            return None
        if len(setups) == 0:
            return self._summarize([], self.initial_capital, anomaly_logs=[])

        original_df = self.df_15m
        self.df_15m = df

        capital = self.initial_capital
        trades: list[dict] = []
        anomaly_logs: list[dict] = []
        cost = self._spread_slippage_price_cost()
        spread_price = self._spread_price()

        for s in setups:
            if capital <= 0:
                anomaly_logs.append(
                    {
                        "symbol": self.symbol,
                        "entry_i": s.get("entry_i"),
                        "reason": "equity_non_positive_stop_backtest",
                        "capital": capital,
                    }
                )
                break

            entry_i = s["entry_i"]
            if entry_i >= len(self.df_15m) - 1:
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "entry_index_out_of_range"}
                )
                continue
            direction = s["direction"]
            entry_raw = float(self.df_15m.iloc[entry_i]["Open"])
            if (not np.isfinite(entry_raw)) or entry_raw <= 0:
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "invalid_entry_price", "entry": entry_raw}
                )
                continue

            entry = entry_raw + cost if direction == "long" else entry_raw - cost

            pivot_stop = self._last_confirmed_pivot(entry_i, direction)
            atr = float(self.df_15m.iloc[entry_i]["atr14"]) if pd.notna(self.df_15m.iloc[entry_i]["atr14"]) else 0.0
            if direction == "long":
                atr_stop = entry - self.config.atr_stop_multiplier * atr if atr > 0 else entry * 0.995
                stop = pivot_stop if pivot_stop is not None else atr_stop
                stop = min(stop, entry - 1e-8)
            else:
                atr_stop = entry + self.config.atr_stop_multiplier * atr if atr > 0 else entry * 1.005
                stop = pivot_stop if pivot_stop is not None else atr_stop
                stop = max(stop, entry + 1e-8)

            stop_dist = abs(entry - stop)
            min_stop_dist = max(
                2.0 * spread_price,
                self.config.min_stop_atr_ratio * atr if atr > 0 else 0.0,
            )
            max_stop_dist = self.config.max_stop_atr_multiple * atr if atr > 0 else np.inf

            if stop_dist <= 1e-12:
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "zero_stop_distance", "entry": entry, "stop": stop}
                )
                continue
            if stop_dist < min_stop_dist:
                anomaly_logs.append(
                    {
                        "symbol": self.symbol,
                        "entry_i": entry_i,
                        "reason": "stop_distance_below_min_threshold",
                        "stop_dist": stop_dist,
                        "min_stop_dist": min_stop_dist,
                        "atr": atr,
                    }
                )
                continue
            if stop_dist >= max_stop_dist:
                anomaly_logs.append(
                    {
                        "symbol": self.symbol,
                        "entry_i": entry_i,
                        "reason": "stop_distance_above_max_atr",
                        "stop_dist": stop_dist,
                        "max_stop_dist": max_stop_dist,
                        "atr": atr,
                    }
                )
                continue

            planned_risk = capital * self.config.risk_per_trade
            if (not np.isfinite(planned_risk)) or planned_risk <= 0:
                anomaly_logs.append(
                    {
                        "symbol": self.symbol,
                        "entry_i": entry_i,
                        "reason": "non_positive_risk_amount",
                        "capital": capital,
                        "risk_money": planned_risk,
                    }
                )
                continue

            size = planned_risk / stop_dist
            max_size = (capital * self.config.max_leverage_per_trade) / max(entry, 1e-12)
            if size > max_size:
                size = max_size
            risk_money = size * stop_dist
            if (not np.isfinite(size)) or size <= 0 or (not np.isfinite(risk_money)) or risk_money <= 0:
                anomaly_logs.append(
                    {
                        "symbol": self.symbol,
                        "entry_i": entry_i,
                        "reason": "invalid_position_size",
                        "size": size,
                        "risk_money": risk_money,
                    }
                )
                continue

            target1_pivot = self._target_pivot(entry_i, direction)
            if direction == "long":
                tp1 = target1_pivot if target1_pivot is not None and target1_pivot > entry else entry + stop_dist
                tp2 = entry + self.config.tp2_rr * stop_dist
            else:
                tp1 = target1_pivot if target1_pivot is not None and target1_pivot < entry else entry - stop_dist
                tp2 = entry - self.config.tp2_rr * stop_dist

            current_stop = stop
            tp1_hit = False
            exit_price = None
            exit_i = None
            exit_reason = None

            end_i = min(len(self.df_15m) - 1, entry_i + self.config.max_holding_bars_15m)
            for i in range(entry_i, end_i + 1):
                row = self.df_15m.iloc[i]
                high = float(row["High"])
                low = float(row["Low"])
                close = float(row["Close"])

                if direction == "long":
                    if (not tp1_hit) and high >= tp1:
                        tp1_hit = True
                        current_stop = entry
                    if high >= tp2:
                        exit_price = tp2 - cost
                        exit_i = i
                        exit_reason = "TP2"
                        break
                    if low <= current_stop:
                        exit_price = current_stop - cost
                        exit_i = i
                        exit_reason = "BE" if tp1_hit else "SL"
                        break
                else:
                    if (not tp1_hit) and low <= tp1:
                        tp1_hit = True
                        current_stop = entry
                    if low <= tp2:
                        exit_price = tp2 + cost
                        exit_i = i
                        exit_reason = "TP2"
                        break
                    if high >= current_stop:
                        exit_price = current_stop + cost
                        exit_i = i
                        exit_reason = "BE" if tp1_hit else "SL"
                        break

                if i == end_i:
                    exit_price = close
                    exit_i = i
                    exit_reason = "TIME"
                    break

            if exit_price is None or exit_i is None:
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "no_exit_found"}
                )
                continue

            split = self.config.tp1_split_ratio
            if direction == "long":
                if tp1_hit:
                    pnl = (tp1 - entry) * size * split + (exit_price - entry) * size * (1 - split)
                else:
                    pnl = (exit_price - entry) * size
            else:
                if tp1_hit:
                    pnl = (entry - tp1) * size * split + (entry - exit_price) * size * (1 - split)
                else:
                    pnl = (entry - exit_price) * size

            rr_multiple = pnl / risk_money if risk_money > 0 else 0.0
            if (not np.isfinite(pnl)) or (not np.isfinite(rr_multiple)):
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "non_finite_trade_result", "pnl": pnl, "rr": rr_multiple}
                )
                continue
            if abs(rr_multiple) > self.config.max_abs_rr_multiple:
                anomaly_logs.append(
                    {"symbol": self.symbol, "entry_i": entry_i, "reason": "rr_multiple_outlier_filtered", "rr": rr_multiple, "pnl": pnl}
                )
                continue

            capital_before = capital
            capital += pnl
            entry_ts = self.df_15m.index[entry_i]
            exit_ts = self.df_15m.index[exit_i]
            trades.append(
                {
                    "entry_i": entry_i,
                    "exit_i": exit_i,
                    "entry_time": entry_ts.isoformat(),
                    "exit_time": exit_ts.isoformat(),
                    "direction": direction,
                    "entry": entry,
                    "exit": exit_price,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp1_hit": tp1_hit,
                    "pnl": pnl,
                    "ret": pnl / capital_before if capital_before > 0 else 0.0,
                    "risk_amount": risk_money,
                    "rr_multiple": rr_multiple,
                    "exit_reason": exit_reason,
                    "signal_reason": s["reason"],
                }
            )
            if max_closed_trades is not None and len(trades) >= max_closed_trades:
                break

        self.df_15m = original_df
        return self._summarize(trades, capital, anomaly_logs=anomaly_logs)

    def _summarize(
        self,
        trades: list[dict],
        final_capital: float,
        anomaly_logs: list[dict] | None = None,
    ) -> dict | None:
        anomaly_logs = anomaly_logs or []
        if len(trades) == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "final_capital": float(final_capital),
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "max_consecutive_losses": 0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "rr": 0.0,
                "trades": [],
                "anomaly_count": len(anomaly_logs),
                "anomaly_logs": anomaly_logs,
            }
        df = pd.DataFrame(trades)
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        profit_factor = (
            wins["pnl"].sum() / abs(losses["pnl"].sum())
            if len(losses) > 0 and losses["pnl"].sum() != 0
            else np.inf
        )

        equity = self.initial_capital + df["pnl"].cumsum()
        peak = equity.cummax()
        dd = (peak - equity) / peak
        max_dd = float(dd.max() * 100 if len(dd) else 0.0)

        ret_series = df["ret"].replace([np.inf, -np.inf], np.nan).dropna()
        sharpe = 0.0
        if len(ret_series) > 1 and ret_series.std() > 0:
            sharpe = float((ret_series.mean() / ret_series.std()) * np.sqrt(252))

        # 连续亏损次数
        max_consec_losses = 0
        cur_losses = 0
        for pnl in df["pnl"]:
            if pnl <= 0:
                cur_losses += 1
                max_consec_losses = max(max_consec_losses, cur_losses)
            else:
                cur_losses = 0

        avg_win = float(wins["pnl"].mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses["pnl"].mean())) if len(losses) > 0 else 0.0
        rr = avg_win / avg_loss if avg_loss > 0 else 0.0

        return {
            "total_trades": int(len(df)),
            "win_rate": float(len(wins) / len(df) * 100),
            "profit_factor": float(profit_factor if np.isfinite(profit_factor) else 0.0),
            "total_pnl": float(df["pnl"].sum()),
            "final_capital": float(final_capital),
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "max_consecutive_losses": int(max_consec_losses),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "rr": float(rr),
            "trades": trades,
            "anomaly_count": len(anomaly_logs),
            "anomaly_logs": anomaly_logs,
        }

    # --------------------------
    # 待办4: 验证套件
    # --------------------------
    def run_validation_suite(self) -> dict:
        """
        输出:
        - 样本内/样本外
        - walk-forward(滚动窗口)结果
        """
        if self.df_15m is None or self.df_15m.empty:
            return {"error": "no_data"}

        n = len(self.df_15m)
        split_i = int(n * 0.7)
        df_is = self.df_15m.iloc[:split_i].copy()
        df_oos = self.df_15m.iloc[split_i:].copy()

        setups_is = self.find_setups(df_is)
        setups_oos = self.find_setups(df_oos)
        res_is = self.run_backtest(setups_is, df_is)
        res_oos = self.run_backtest(setups_oos, df_oos)

        wf_results = []
        train = int(n * 0.5)
        test = int(n * 0.2)
        step = int(n * 0.15)
        start = 0
        fold = 1
        while start + train + test <= n and fold <= 4:
            tr = self.df_15m.iloc[start : start + train].copy()
            te = self.df_15m.iloc[start + train : start + train + test].copy()

            tr_setups = self.find_setups(tr)
            te_setups = self.find_setups(te)
            tr_res = self.run_backtest(tr_setups, tr)
            te_res = self.run_backtest(te_setups, te)
            wf_results.append(
                {
                    "fold": fold,
                    "train_trades": tr_res["total_trades"] if tr_res else 0,
                    "train_win_rate": tr_res["win_rate"] if tr_res else 0.0,
                    "test_trades": te_res["total_trades"] if te_res else 0,
                    "test_win_rate": te_res["win_rate"] if te_res else 0.0,
                    "test_pf": te_res["profit_factor"] if te_res else 0.0,
                    "test_max_dd": te_res["max_drawdown"] if te_res else 0.0,
                }
            )
            start += step
            fold += 1

        return {"in_sample": res_is, "out_of_sample": res_oos, "walk_forward": wf_results}

    # --------------------------
    # 报告与图表导出
    # --------------------------
    def plot_single_trade_chart(self, trade: dict, trade_num: int, chart_dir: str) -> str | None:
        if self.df_15m is None or self.df_15m.empty:
            return None
        entry_i = int(trade["entry_i"])
        exit_i = int(trade["exit_i"])
        start_i = max(0, entry_i - 40)
        end_i = min(len(self.df_15m), exit_i + 20)
        d = self.df_15m.iloc[start_i:end_i].copy()
        if d.empty:
            return None

        fig, ax = plt.subplots(figsize=(14, 6))
        x = mdates.date2num(d.index.to_pydatetime())
        bar_width = 0.008
        for idx, (_, row) in enumerate(d.iterrows()):
            open_p = float(row["Open"])
            high_p = float(row["High"])
            low_p = float(row["Low"])
            close_p = float(row["Close"])
            color = "#26a69a" if close_p >= open_p else "#ef5350"
            ax.plot([x[idx], x[idx]], [low_p, high_p], color=color, linewidth=0.8)
            body_bottom = min(open_p, close_p)
            body_h = max(abs(close_p - open_p), 1e-8)
            rect = plt.Rectangle(
                (x[idx] - bar_width / 2, body_bottom),
                bar_width,
                body_h,
                facecolor=color,
                edgecolor=color,
                alpha=0.85,
            )
            ax.add_patch(rect)

        if "ema_20" in d.columns:
            ax.plot(d.index, d["ema_20"], color="#1f77b4", linewidth=1.2, label="EMA20")
        if "ema_50" in d.columns:
            ax.plot(d.index, d["ema_50"], color="#ff7f0e", linewidth=1.2, label="EMA50")
        if "ema_100" in d.columns:
            ax.plot(d.index, d["ema_100"], color="#9467bd", linewidth=1.2, label="EMA100")

        entry_time = pd.to_datetime(trade["entry_time"])
        exit_time = pd.to_datetime(trade["exit_time"])
        entry_price = float(trade["entry"])
        exit_price = float(trade["exit"])

        ax.scatter(entry_time, entry_price, s=140, marker="^", color="blue", edgecolors="white", zorder=5)
        ax.scatter(
            exit_time,
            exit_price,
            s=140,
            marker="X",
            color=("green" if trade["pnl"] > 0 else "red"),
            edgecolors="white",
            zorder=6,
        )
        ax.annotate(
            f"Entry\n{entry_price:.5f}",
            (entry_time, entry_price),
            xytext=(10, 20),
            textcoords="offset points",
            fontsize=8,
            bbox={"boxstyle": "round,pad=0.3", "fc": "#dbeafe", "alpha": 0.8},
        )
        ax.annotate(
            f"Exit({trade['exit_reason']})\n{exit_price:.5f}",
            (exit_time, exit_price),
            xytext=(10, -30),
            textcoords="offset points",
            fontsize=8,
            bbox={
                "boxstyle": "round,pad=0.3",
                "fc": ("#dcfce7" if trade["pnl"] > 0 else "#fee2e2"),
                "alpha": 0.8,
            },
        )

        ax.set_title(
            f"{self.symbol} Trade#{trade_num:03d} {trade['direction'].upper()} "
            f"PnL={trade['pnl']:.2f} RR={trade.get('rr_multiple', 0.0):.2f}R"
        )
        ax.set_ylabel("Price")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()

        Path(chart_dir).mkdir(parents=True, exist_ok=True)
        file_path = Path(chart_dir) / f"{self.symbol}_trade_{trade_num:03d}.png"
        fig.savefig(file_path, dpi=140)
        plt.close(fig)
        return str(file_path)


def _build_output_root() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path("reports") / f"forex_15m_report_{ts}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "trade_charts").mkdir(parents=True, exist_ok=True)
    return root


def _export_excel_report(all_results: list[dict], output_root: Path) -> str:
    summary_rows = []
    trade_rows = []
    wf_rows = []
    anomaly_rows = []
    for rec in all_results:
        sym = rec["symbol"]
        r = rec["result"]
        v = rec["validation"]
        ret_pct = (r["final_capital"] - 100000.0) / 100000.0 * 100
        summary_rows.append(
            {
                "symbol": sym,
                "total_trades": r["total_trades"],
                "win_rate_pct": round(r["win_rate"], 3),
                "profit_factor": round(r["profit_factor"], 4),
                "max_drawdown_pct": round(r["max_drawdown"], 3),
                "sharpe": round(r["sharpe"], 4),
                "total_pnl": round(r["total_pnl"], 4),
                "return_pct": round(ret_pct, 4),
                "max_consecutive_losses": r["max_consecutive_losses"],
                "anomaly_count": r.get("anomaly_count", 0),
            }
        )
        for i, t in enumerate(r["trades"], 1):
            trade_rows.append(
                {
                    "symbol": sym,
                    "trade_no": i,
                    "entry_time": t["entry_time"],
                    "exit_time": t["exit_time"],
                    "direction": t["direction"],
                    "entry_price": t["entry"],
                    "exit_price": t["exit"],
                    "stop_price": t["stop"],
                    "tp1_price": t["tp1"],
                    "tp2_price": t["tp2"],
                    "risk_amount": t["risk_amount"],
                    "pnl": t["pnl"],
                    "rr_multiple": t["rr_multiple"],
                    "result": "WIN" if t["pnl"] > 0 else "LOSS",
                    "exit_reason": t["exit_reason"],
                }
            )
        for row in v.get("walk_forward", []):
            wf_rows.append({"symbol": sym, **row})
        for row in r.get("anomaly_logs", []):
            anomaly_rows.append({"symbol": sym, **row})

    summary_df = pd.DataFrame(summary_rows)
    trades_df = pd.DataFrame(trade_rows)
    wf_df = pd.DataFrame(wf_rows)
    anomaly_df = pd.DataFrame(anomaly_rows)

    xlsx_path = output_root / "回测报告.xlsx"
    try:
        with pd.ExcelWriter(xlsx_path) as writer:
            summary_df.to_excel(writer, sheet_name="summary", index=False)
            trades_df.to_excel(writer, sheet_name="trades", index=False)
            wf_df.to_excel(writer, sheet_name="walk_forward", index=False)
            anomaly_df.to_excel(writer, sheet_name="anomaly_logs", index=False)
        return str(xlsx_path)
    except Exception:
        summary_df.to_csv(output_root / "summary.csv", index=False, encoding="utf-8-sig")
        trades_df.to_csv(output_root / "trades.csv", index=False, encoding="utf-8-sig")
        wf_df.to_csv(output_root / "walk_forward.csv", index=False, encoding="utf-8-sig")
        anomaly_df.to_csv(output_root / "anomaly_logs.csv", index=False, encoding="utf-8-sig")
        return str(output_root / "summary.csv")


def _export_text_report(all_results: list[dict], output_root: Path, excel_path: str, chart_count: int) -> str:
    lines = []
    lines.append("# 15分钟外汇策略回测报告")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 报告目录: {output_root.resolve()}")
    lines.append(f"- Excel/统计文件: {excel_path}")
    lines.append(f"- 交易图数量: {chart_count}")
    lines.append(f"- 异常过滤总数: {sum(int(x['result'].get('anomaly_count', 0)) for x in all_results)}")
    lines.append("")
    lines.append("## 品种汇总")
    lines.append("")
    lines.append("| 品种 | 交易数 | 异常过滤 | 胜率 | PF | 最大回撤 | 夏普 | 收益率 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for rec in all_results:
        sym = rec["symbol"]
        r = rec["result"]
        ret_pct = (r["final_capital"] - 100000.0) / 100000.0 * 100
        lines.append(
            f"| {sym} | {r['total_trades']} | {r.get('anomaly_count', 0)} | {r['win_rate']:.2f}% | {r['profit_factor']:.2f} | "
            f"{r['max_drawdown']:.2f}% | {r['sharpe']:.2f} | {ret_pct:.2f}% |"
        )
    report_path = output_root / "回测报告.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


def run_15min_strategy(
    api_key: str = "",
    symbols_override: list[str] | None = None,
    max_trades_per_symbol: int | None = None,
) -> list[dict]:
    api_key = api_key or TWELVEDATA_API_KEY
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=220)).strftime("%Y-%m-%d")
    symbols = symbols_override or ["XAUUSD", "XAGUSD", "EURUSD", "USDJPY", "AUDUSD"]

    print("=" * 80)
    print("15分钟外汇/贵金属策略回测")
    print("=" * 80)
    print(f"回测区间: {start_date} ~ {end_date}")
    print("策略主链: 多周期趋势 -> 20K突破 -> 回调EMA20 -> 反转触发 -> 风控出场")
    print("风险基线: 2%/笔")
    print("=" * 80)
    output_root = _build_output_root()
    print(f"输出目录: {output_root.resolve()}")

    all_results = []
    total_chart_count = 0
    for sym in symbols:
        print(f"\n[{sym}] 开始...")
        bt = Forex15MinBacktester(
            symbol=sym,
            start_date=start_date,
            end_date=end_date,
            initial_capital=100000.0,
            config=StrategyConfig(risk_per_trade=0.02),
        )
        bt.print_rule_audit()
        df = bt.load_data_twelvedata(api_key=api_key)
        if df is None or len(df) < 300:
            print(f"[{sym}] 数据不足，跳过")
            continue

        setups = bt.find_setups()
        print(f"[{sym}] 信号数: {len(setups)}")
        if max_trades_per_symbol is not None:
            print(f"[{sym}] 单笔/限量模式: 目标成交 {max_trades_per_symbol} 笔")
        result = bt.run_backtest(setups, max_closed_trades=max_trades_per_symbol)
        validation = bt.run_validation_suite()

        if result is None:
            print(f"[{sym}] 无有效交易")
            continue

        ret_pct = (result["final_capital"] - 100000.0) / 100000.0 * 100
        print(
            f"[{sym}] 交易={result['total_trades']} 胜率={result['win_rate']:.1f}% "
            f"PF={result['profit_factor']:.2f} DD={result['max_drawdown']:.1f}% "
            f"夏普={result['sharpe']:.2f} 收益={ret_pct:.1f}%"
        )

        oos = validation.get("out_of_sample")
        if oos:
            print(
                f"[{sym}] OOS: 交易={oos['total_trades']} 胜率={oos['win_rate']:.1f}% "
                f"PF={oos['profit_factor']:.2f} DD={oos['max_drawdown']:.1f}%"
            )
        print(f"[{sym}] WalkForward折数: {len(validation.get('walk_forward', []))}")

        symbol_chart_dir = output_root / "trade_charts" / sym
        chart_files = []
        for idx, tr in enumerate(result["trades"], 1):
            fp = bt.plot_single_trade_chart(tr, idx, str(symbol_chart_dir))
            if fp:
                chart_files.append(fp)
        total_chart_count += len(chart_files)
        print(f"[{sym}] 已输出交易图: {len(chart_files)} 张")

        result["chart_files"] = chart_files
        all_results.append({"symbol": sym, "result": result, "validation": validation})
        time.sleep(1)

    print("\n" + "=" * 80)
    print("汇总")
    print("=" * 80)
    if not all_results:
        print("无有效结果。将生成空白报告模板（便于后续重跑覆盖）。")
        excel_path = _export_excel_report(all_results, output_root)
        md_report_path = _export_text_report(all_results, output_root, excel_path, total_chart_count)
        print("\n报告文件已生成:")
        print(f"- Markdown报告: {md_report_path}")
        print(f"- Excel/统计:   {excel_path}")
        print(f"- 交易图目录:   {(output_root / 'trade_charts').resolve()}")
        return []

    print(f"{'品种':<10} {'交易数':>8} {'胜率':>8} {'PF':>8} {'最大回撤':>10} {'收益率':>10}")
    print("-" * 60)
    for r in all_results:
        m = r["result"]
        ret_pct = (m["final_capital"] - 100000.0) / 100000.0 * 100
        print(
            f"{r['symbol']:<10} {m['total_trades']:>8} {m['win_rate']:>7.1f}% "
            f"{m['profit_factor']:>8.2f} {m['max_drawdown']:>9.1f}% {ret_pct:>9.1f}%"
        )

    excel_path = _export_excel_report(all_results, output_root)
    md_report_path = _export_text_report(all_results, output_root, excel_path, total_chart_count)
    print("\n报告文件已生成:")
    print(f"- Markdown报告: {md_report_path}")
    print(f"- Excel/统计:   {excel_path}")
    print(f"- 交易图目录:   {(output_root / 'trade_charts').resolve()}")

    return all_results


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="15分钟外汇策略回测")
        parser.add_argument(
            "--symbol",
            type=str,
            default=None,
            help="指定单个品种，例如 EURUSD / XAUUSD",
        )
        parser.add_argument(
            "--single",
            action="store_true",
            help="单笔回测模式（每个品种只回测1笔）",
        )
        parser.add_argument(
            "--max-trades",
            type=int,
            default=None,
            help="每个品种最大回测笔数，如 1/5/10",
        )
        args = parser.parse_args()

        symbol_list = [args.symbol.upper()] if args.symbol else None
        max_trades = args.max_trades
        if args.single and max_trades is None:
            max_trades = 1

        run_15min_strategy(symbols_override=symbol_list, max_trades_per_symbol=max_trades)
    except KeyboardInterrupt:
        print("\n手动停止。")
    except Exception as e:
        print(f"\n运行异常: {e}")
        import traceback

        traceback.print_exc()
