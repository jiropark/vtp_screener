"""기술적 지표 계산 모듈.

VTP 스크리너에서 사용하는 핵심 지표:
  - ATR (Average True Range)
  - VWAP (Volume Weighted Average Price)
  - 볼린저 밴드
  - 거래량 비율 / 추세 / 최대치
  - 종가 퀄리티 (Close Quality)
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
