"""기술적 지표 계산 모듈.

VTP 스크리너에서 사용하는 핵심 지표:
  - ATR (Average True Range)
  - VWAP (Volume Weighted Average Price)
  - 볼린저 밴드
  - 거래량 비율 / 추세 / 최대치
  - 종가 퀄리티 (Close Quality)
  - 선형회귀 R² (순항도)
  - 이동평균 정배열
  - 고점 대비 낙폭
  - ATR 추세 (변동성 축소)
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def calc_atr(ohlcv_list: list[dict], period: int = 14) -> float:
    """ATR (Average True Range) 계산.

    Parameters
    ----------
    ohlcv_list : list[dict]
        OHLCV 데이터. 각 dict에 high, low, close 키 필요.
        날짜 오름차순 (과거→최근) 정렬 가정.
    period : int
        ATR 기간 (기본 14일).

    Returns
    -------
    float
        ATR 값. 데이터 부족 시 0.0.
    """
    if not ohlcv_list or len(ohlcv_list) < 2:
        return 0.0

    true_ranges: list[float] = []

    for i in range(1, len(ohlcv_list)):
        high = ohlcv_list[i].get("high", 0)
        low = ohlcv_list[i].get("low", 0)
        prev_close = ohlcv_list[i - 1].get("close", 0)

        # True Range = max(H-L, |H-Prev_C|, |L-Prev_C|)
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # 최근 period개의 TR 평균 (Wilder 방식 단순화)
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def calc_vwap(intraday_data: list[dict]) -> float:
    """당일 VWAP (Volume Weighted Average Price) 계산.

    Parameters
    ----------
    intraday_data : list[dict]
        분봉/틱 데이터. 각 dict에 price (체결가), volume (체결량) 필요.
        또는 high, low, close, volume 으로 typical price 계산.

    Returns
    -------
    float
        VWAP. 데이터 없으면 0.0.
    """
    if not intraday_data:
        return 0.0

    cum_pv = 0.0  # 누적 (가격 * 거래량)
    cum_vol = 0.0  # 누적 거래량

    for bar in intraday_data:
        vol = bar.get("volume", 0)
        if vol <= 0:
            continue

        # typical price 사용 (H+L+C)/3, 없으면 price
        if "high" in bar and "low" in bar and "close" in bar:
            typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        else:
            typical = bar.get("price", 0)

        if typical <= 0:
            continue

        cum_pv += typical * vol
        cum_vol += vol

    if cum_vol <= 0:
        return 0.0

    return cum_pv / cum_vol


def calc_bollinger(
    closes: list[float],
    period: int = 20,
    std: float = 2.0,
) -> tuple[float, float, float]:
    """볼린저 밴드 계산.

    Parameters
    ----------
    closes : list[float]
        종가 리스트 (날짜 오름차순).
    period : int
        이동평균 기간 (기본 20일).
    std : float
        표준편차 배수 (기본 2.0).

    Returns
    -------
    tuple[float, float, float]
        (upper, middle, lower). 데이터 부족 시 (0.0, 0.0, 0.0).
    """
    if not closes or len(closes) < period:
        return (0.0, 0.0, 0.0)

    recent = closes[-period:]
    middle = sum(recent) / period

    # 표준편차 계산
    variance = sum((x - middle) ** 2 for x in recent) / period
    stddev = math.sqrt(variance)

    upper = middle + std * stddev
    lower = middle - std * stddev

    return (upper, middle, lower)


def calc_volume_ratio(
    volumes: list[float],
    threshold_percentile: int = 90,
    lookback: int = 60,
) -> tuple[float, bool]:
    """거래량 비율 및 상위 퍼센타일 돌파 여부.

    Parameters
    ----------
    volumes : list[float]
        거래량 리스트 (날짜 오름차순). 마지막 값이 당일.
    threshold_percentile : int
        상위 퍼센타일 기준 (기본 90 → 상위 10%).
    lookback : int
        비교 기간 (기본 60일).

    Returns
    -------
    tuple[float, bool]
        (ratio, is_above_threshold).
        ratio = 당일 거래량 / lookback 평균 거래량.
        is_above_threshold = 당일 거래량이 퍼센타일 초과.
    """
    if not volumes or len(volumes) < 2:
        return (0.0, False)

    current_vol = volumes[-1]
    # lookback 기간 (당일 제외)
    past = volumes[-(lookback + 1):-1] if len(volumes) > lookback else volumes[:-1]

    if not past:
        return (0.0, False)

    avg_vol = sum(past) / len(past)
    ratio = current_vol / avg_vol if avg_vol > 0 else 0.0

    # 퍼센타일 계산
    sorted_past = sorted(past)
    idx = int(len(sorted_past) * threshold_percentile / 100)
    idx = min(idx, len(sorted_past) - 1)
    threshold_val = sorted_past[idx]

    is_above = current_vol > threshold_val

    return (round(ratio, 2), is_above)


def calc_close_quality(high: float, low: float, close: float) -> float:
    """종가 퀄리티 = (close - low) / (high - low).

    1.0에 가까울수록 고가 근처에서 마감 (강한 마감).
    0.0에 가까울수록 저가 근처에서 마감.

    Parameters
    ----------
    high : float
        당일 고가.
    low : float
        당일 저가.
    close : float
        당일 종가.

    Returns
    -------
    float
        0.0 ~ 1.0 범위. high == low면 0.5 반환.
    """
    spread = high - low
    if spread <= 0:
        return 0.5  # 변동 없는 경우 중립

    quality = (close - low) / spread
    return max(0.0, min(1.0, quality))


def calc_volume_trend(volumes: list[float], days: int = 3) -> bool:
    """N일 연속 거래량 증가 여부.

    Parameters
    ----------
    volumes : list[float]
        거래량 리스트 (날짜 오름차순). 마지막이 당일.
    days : int
        연속 증가 확인 일수 (기본 3일).

    Returns
    -------
    bool
        True면 N일 연속 증가.
    """
    if not volumes or len(volumes) < days:
        return False

    recent = volumes[-days:]
    for i in range(1, len(recent)):
        if recent[i] <= recent[i - 1]:
            return False

    return True


def calc_max_volume(trade_amounts: list[float], days: int = 60) -> bool:
    """당일 거래대금이 최근 N일 최대치를 돌파했는지.

    Parameters
    ----------
    trade_amounts : list[float]
        거래대금 리스트 (날짜 오름차순). 마지막이 당일.
    days : int
        비교 기간 (기본 60일).

    Returns
    -------
    bool
        True면 당일이 최근 N일 최대.
    """
    if not trade_amounts or len(trade_amounts) < 2:
        return False

    current = trade_amounts[-1]
    # 비교 대상: 당일 제외 최근 days일
    past = trade_amounts[-(days + 1):-1] if len(trade_amounts) > days else trade_amounts[:-1]

    if not past:
        return False

    return current > max(past)


def calc_linear_regression_r2(closes: list[float], period: int = 20) -> tuple[float, bool]:
    """선형회귀 R² (결정계수) — 추세 일관성 측정.

    R²가 1에 가까우면 가격이 직선에 가깝게 움직임 (= 순항).
    R²가 0에 가까우면 방향 없이 랜덤하게 움직임.

    Parameters
    ----------
    closes : list[float]
        종가 리스트 (날짜 오름차순).
    period : int
        회귀 계산 기간 (기본 20일).

    Returns
    -------
    tuple[float, bool]
        (r_squared, slope_positive).
        r_squared: 0.0 ~ 1.0 범위의 결정계수.
        slope_positive: 기울기가 양수이면 True (상승 추세).
    """
    if not closes or len(closes) < period:
        return (0.0, False)

    recent = closes[-period:]
    n = len(recent)

    # x = 0, 1, 2, ..., n-1
    x_mean = (n - 1) / 2
    y_mean = sum(recent) / n

    ss_xy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(recent))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    ss_yy = sum((y - y_mean) ** 2 for y in recent)

    if ss_xx == 0 or ss_yy == 0:
        return (0.0, False)

    slope = ss_xy / ss_xx
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

    return (round(r_squared, 4), slope > 0)


def calc_ma_alignment(
    closes: list[float],
    short: int = 5,
    mid: int = 20,
    long: int = 60,
) -> dict:
    """이동평균 정배열 여부 및 현재가 대비 이평선 거리.

    정배열: 현재가 > MA5 > MA20 > MA60

    Parameters
    ----------
    closes : list[float]
        종가 리스트 (날짜 오름차순).
    short : int
        단기 이평 기간 (기본 5).
    mid : int
        중기 이평 기간 (기본 20).
    long : int
        장기 이평 기간 (기본 60).

    Returns
    -------
    dict
        {
            "ma_short": float, "ma_mid": float, "ma_long": float,
            "aligned": bool,  # 정배열 여부
            "dist_to_short_pct": float,  # 현재가와 단기이평 괴리율 (%)
        }
    """
    result = {
        "ma_short": 0,
        "ma_mid": 0,
        "ma_long": 0,
        "aligned": False,
        "dist_to_short_pct": 0.0,
    }

    if not closes or len(closes) < long:
        return result

    ma_s = sum(closes[-short:]) / short
    ma_m = sum(closes[-mid:]) / mid
    ma_l = sum(closes[-long:]) / long

    current = closes[-1]

    result["ma_short"] = round(ma_s, 2)
    result["ma_mid"] = round(ma_m, 2)
    result["ma_long"] = round(ma_l, 2)
    result["aligned"] = current > ma_s > ma_m > ma_l
    result["dist_to_short_pct"] = round((current - ma_s) / ma_s * 100, 2) if ma_s > 0 else 0.0

    return result


def calc_drawdown_from_high(closes: list[float], period: int = 20) -> float:
    """최근 N일 고점 대비 현재 낙폭 (%).

    0이면 신고가, -5이면 고점 대비 5% 하락.

    Parameters
    ----------
    closes : list[float]
        종가 리스트 (날짜 오름차순).
    period : int
        고점 탐색 기간 (기본 20일).

    Returns
    -------
    float
        0 이하의 값 (%). 예: -3.5 → 고점 대비 3.5% 하락.
    """
    if not closes or len(closes) < 2:
        return 0.0

    recent = closes[-period:] if len(closes) >= period else closes
    peak = max(recent)
    current = closes[-1]

    if peak <= 0:
        return 0.0

    return round((current - peak) / peak * 100, 2)


def calc_atr_trend(ohlcv_list: list[dict], period: int = 14, lookback: int = 5) -> bool:
    """ATR이 축소 추세인지 (최근 lookback일).

    변동성 축소 = 안정적 순항 신호.
    최근 ATR < lookback일 전 ATR이면 True.

    Parameters
    ----------
    ohlcv_list : list[dict]
        OHLCV 데이터 (날짜 오름차순).
    period : int
        ATR 기간 (기본 14).
    lookback : int
        비교 기간 (기본 5일).

    Returns
    -------
    bool
        True면 ATR 축소 추세 (변동성 감소).
    """
    if not ohlcv_list or len(ohlcv_list) < period + lookback:
        return False

    # 현재 ATR
    atr_now = calc_atr(ohlcv_list, period)
    # lookback일 전 ATR
    atr_before = calc_atr(ohlcv_list[:-lookback], period)

    if atr_before <= 0:
        return False

    return atr_now < atr_before
