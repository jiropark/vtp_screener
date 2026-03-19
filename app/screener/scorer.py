"""VTP 스코어링 엔진 — 종목 점수 산출 (0~100점).

점수 구성:
  - volume_score (0~40): 거래량 이상 감지
  - price_score  (0~40): 가격 돌파 시그널
  - supply_bonus (0~10): 수급 우위 보너스
  - 합계 최대 100점 (90 = 40+40+10 + 10점 여유)

개별 항목:
  volume_score:
    - 섹터/시총 기준 상위 10% 거래량 비율: 20점
    - 3일 연속 거래량 증가: 10점
    - 거래대금 60일 최대 돌파: 10점

  price_score:
    - 종가 > 20일 고가: 15점 (주요 시그널)
    - 종가 > VWAP * 1.02: 15점 (주요 시그널)
    - 종가 > 볼린저 상단: 5점 (보조)
    - 종가 퀄리티 >= 0.8: 5점 (보조)

  supply_bonus:
    - 외국인 또는 기관 대량 순매수 (상위 5%): 10점
"""

import logging
from typing import Any, Optional

from app.screener.indicators import (
    calc_atr,
    calc_bollinger,
    calc_close_quality,
    calc_max_volume,
    calc_volume_ratio,
    calc_volume_trend,
    calc_vwap,
)

logger = logging.getLogger(__name__)

# 수급 보너스 판정 기준 (상위 5% → percentile 95)
SUPPLY_NET_BUY_PERCENTILE = 95


def score_stock(
    code: str,
    ohlcv: list[dict],
    investor_data: Optional[list[dict]] = None,
    intraday_data: Optional[list[dict]] = None,
    sector_volume_pctile: Optional[float] = None,
) -> dict:
    """종목 점수를 산출한다.

    Parameters
    ----------
    code : str
        종목코드.
    ohlcv : list[dict]
        일봉 OHLCV (날짜 오름차순, 최소 60일).
        각 dict: date, open, high, low, close, volume, trade_amount.
    investor_data : list[dict], optional
        투자자별 순매수. date, foreign_net, inst_net.
    intraday_data : list[dict], optional
        분봉 데이터 (VWAP 계산용).
    sector_volume_pctile : float, optional
        해당 종목의 섹터/시총 기준 거래량 퍼센타일 (0~100).
        외부에서 미리 계산한 경우 사용.

    Returns
    -------
    dict
        total_score, volume_score, price_score, supply_bonus,
        indicators (개별 지표 값), details (채점 내역).
    """
    result = {
        "code": code,
        "total_score": 0,
        "volume_score": 0,
        "price_score": 0,
        "supply_bonus": 0,
        "indicators": {},
        "details": [],
    }

    if not ohlcv or len(ohlcv) < 5:
        result["details"].append("데이터 부족 (OHLCV < 5일)")
        return result

    today = ohlcv[-1]
    close = today.get("close", 0)
    high = today.get("high", 0)
    low = today.get("low", 0)

    if close <= 0:
        result["details"].append("종가 0 이하")
        return result

    # ── 지표 계산 ──────────────────────────────────────────

    # ATR
    atr = calc_atr(ohlcv, period=14)

    # 종가 리스트
    closes = [d.get("close", 0) for d in ohlcv]
    volumes = [d.get("volume", 0) for d in ohlcv]
    trade_amounts = [d.get("trade_amount", 0) for d in ohlcv]

    # 볼린저 밴드
    bb_upper, bb_middle, bb_lower = calc_bollinger(closes, period=20, std=2.0)

    # VWAP (분봉 데이터 있으면 사용, 없으면 일봉 기반 근사)
    if intraday_data:
        vwap = calc_vwap(intraday_data)
    else:
        # 일봉 근사 VWAP: (H+L+C)/3 * V 기반
        vwap = calc_vwap([today])

    # 거래량 비율
    vol_ratio, vol_above_threshold = calc_volume_ratio(
        volumes, threshold_percentile=90, lookback=60,
    )

    # 3일 연속 거래량 증가
    vol_trend_3d = calc_volume_trend(volumes, days=3)

    # 거래대금 60일 최대
    max_vol_60d = calc_max_volume(trade_amounts, days=60)

    # 종가 퀄리티
    close_quality = calc_close_quality(high, low, close)

    # 20일 고가 (오늘 제외 — 오늘 포함 시 close > high_20d가 불가능)
    if len(ohlcv) >= 21:
        high_20d = max(d.get("high", 0) for d in ohlcv[-21:-1])
    elif len(ohlcv) >= 2:
        high_20d = max(d.get("high", 0) for d in ohlcv[:-1])
    else:
        high_20d = high  # 데이터 1일뿐이면 오늘 고가 사용

    # 지표 저장
    indicators = {
        "atr": round(atr, 2),
        "vwap": round(vwap, 2),
        "bb_upper": round(bb_upper, 2),
        "bb_middle": round(bb_middle, 2),
        "bb_lower": round(bb_lower, 2),
        "volume_ratio": vol_ratio,
        "volume_above_90pct": vol_above_threshold,
        "volume_trend_3d": vol_trend_3d,
        "max_volume_60d": max_vol_60d,
        "close_quality": round(close_quality, 4),
        "high_20d": high_20d,
        "close": close,
        "high": high,
        "low": low,
    }
    result["indicators"] = indicators

    # ── 거래량 점수 (0~40) ─────────────────────────────────

    volume_score = 0
    details: list[str] = []

    # 1) 섹터/시총 기준 상위 10% 거래량 (20점)
    #    sector_volume_pctile 이 주어지면 사용, 아니면 vol_above_threshold 사용
    if sector_volume_pctile is not None:
        if sector_volume_pctile >= 90:
            volume_score += 20
            details.append(f"섹터 거래량 상위 {100 - sector_volume_pctile:.0f}%: +20")
    elif vol_above_threshold:
        volume_score += 20
        details.append(f"거래량 상위 10% (ratio {vol_ratio}x): +20")

    # 2) 3일 연속 거래량 증가 (10점)
    if vol_trend_3d:
        volume_score += 10
        details.append("3일 연속 거래량 증가: +10")

    # 3) 거래대금 60일 최대 돌파 (10점)
    if max_vol_60d:
        volume_score += 10
        details.append("거래대금 60일 최대 돌파: +10")

    result["volume_score"] = volume_score

    # ── 가격 점수 (0~40) ───────────────────────────────────

    price_score = 0

    # 1) 종가 > 20일 고가 (15점) — 주요 시그널
    if close > high_20d:
        price_score += 15
        details.append(f"종가({close:,}) > 20일 고가({high_20d:,}): +15")

    # 2) 종가 > VWAP * 1.02 (15점) — 주요 시그널
    if vwap > 0 and close > vwap * 1.02:
        price_score += 15
        details.append(f"종가({close:,}) > VWAP({vwap:,.0f})×1.02: +15")

    # 3) 종가 > 볼린저 상단 (5점) — 보조
    if bb_upper > 0 and close > bb_upper:
        price_score += 5
        details.append(f"종가 > BB상단({bb_upper:,.0f}): +5")

    # 4) 종가 퀄리티 >= 0.8 (5점) — 보조: 고가 마감
    if close_quality >= 0.8:
        price_score += 5
        details.append(f"종가 퀄리티 {close_quality:.2f} >= 0.8: +5")

    result["price_score"] = price_score

    # ── 수급 보너스 (0~10) ─────────────────────────────────

    supply_bonus = 0

    if investor_data and len(investor_data) >= 1:
        # 최근 1일 기준 (당일)
        today_inv = investor_data[0]  # 최신 데이터 = 인덱스 0 가정
        foreign_net = today_inv.get("foreign_net", 0)
        inst_net = today_inv.get("inst_net", 0)

        # 외국인 또는 기관 대량 순매수
        # foreign_net/inst_net은 순매수 수량(주), volume은 거래량(주)
        today_volume = today.get("volume", 0)
        if today_volume > 0:
            # 순매수 비율 = 순매수 수량 / 거래량 (동일 단위: 주)
            foreign_ratio = foreign_net / today_volume if foreign_net > 0 else 0
            inst_ratio = inst_net / today_volume if inst_net > 0 else 0

            # 거래량 대비 5% 이상 순매수 → 대량 순매수로 판정
            if foreign_ratio >= 0.05 or inst_ratio >= 0.05:
                supply_bonus = 10
                who = []
                if foreign_ratio >= 0.05:
                    who.append(f"외국인({foreign_ratio:.1%})")
                if inst_ratio >= 0.05:
                    who.append(f"기관({inst_ratio:.1%})")
                details.append(f"대량 순매수 {'+'.join(who)}: +10")

        indicators["foreign_net"] = foreign_net
        indicators["inst_net"] = inst_net

    result["supply_bonus"] = supply_bonus

    # ── 합산 ───────────────────────────────────────────────

    total = volume_score + price_score + supply_bonus
    result["total_score"] = min(total, 100)  # 최대 100점
    result["details"] = details

    logger.debug(
        "스코어 %s: 총 %d (거래량 %d + 가격 %d + 수급 %d)",
        code, result["total_score"], volume_score, price_score, supply_bonus,
    )

    return result
