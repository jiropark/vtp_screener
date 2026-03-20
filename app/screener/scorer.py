"""VTP 스코어링 엔진 — 목표가 순항 전략 (0~100점).

점수 구성:
  - target_score  (0~35): 목표가 괴리율 (상승여력)
  - cruise_score  (0~40): 순항도 (추세 일관성 + 이평 정배열 + 낙폭 + 변동성)
  - supply_score  (0~15): 수급 (외국인/기관 순매수 일수 비율)
  - volume_score  (0~10): 적정 거래량 (과열/부족 아닌 건강한 거래량)
  - 합계 최대 100점
"""

import logging
from typing import Optional

from app.screener.indicators import (
    calc_atr,
    calc_drawdown_from_high,
    calc_linear_regression_r2,
    calc_ma_alignment,
    calc_atr_trend,
    calc_volume_ratio,
    calc_vwap,
)

logger = logging.getLogger(__name__)


def score_stock(
    code: str,
    ohlcv: list[dict],
    target_info: Optional[dict] = None,
    investor_data: Optional[list[dict]] = None,
    intraday_data: Optional[list[dict]] = None,
) -> dict:
    """종목 점수를 산출한다 (목표가 순항 전략).

    Parameters
    ----------
    code : str
        종목코드.
    ohlcv : list[dict]
        일봉 OHLCV (날짜 오름차순, 최소 60일).
    target_info : dict, optional
        네이버 컨센서스 목표가 정보.
        {target_price, current_price, upside_pct, analyst_count, consensus}
    investor_data : list[dict], optional
        투자자별 순매수 (최근 10일). [{date, foreign_net, inst_net}, ...]
    intraday_data : list[dict], optional
        분봉 데이터 (VWAP 계산용).

    Returns
    -------
    dict
        total_score, target_score, cruise_score, supply_score, volume_score,
        indicators, details.
    """
    result = {
        "code": code,
        "total_score": 0,
        "target_score": 0,
        "cruise_score": 0,
        "supply_score": 0,
        "volume_score": 0,
        "indicators": {},
        "details": [],
    }

    if not ohlcv or len(ohlcv) < 20:
        result["details"].append("데이터 부족 (OHLCV < 20일)")
        return result

    today = ohlcv[-1]
    close = today.get("close", 0)

    if close <= 0:
        result["details"].append("종가 0 이하")
        return result

    closes = [d.get("close", 0) for d in ohlcv]
    volumes = [d.get("volume", 0) for d in ohlcv]

    # ── 지표 계산 ──────────────────────────────────────────

    atr = calc_atr(ohlcv, period=14)
    r2, slope_positive = calc_linear_regression_r2(closes, period=20)
    ma_info = calc_ma_alignment(closes, short=5, mid=20, long=60)
    drawdown = calc_drawdown_from_high(closes, period=20)
    atr_shrinking = calc_atr_trend(ohlcv, period=14, lookback=5)
    vol_ratio, _ = calc_volume_ratio(volumes, threshold_percentile=90, lookback=60)

    # VWAP
    if intraday_data:
        vwap = calc_vwap(intraday_data)
    else:
        vwap = calc_vwap([today])

    indicators = {
        "atr": round(atr, 2),
        "vwap": round(vwap, 2),
        "r2": r2,
        "slope_positive": slope_positive,
        "ma_short": ma_info["ma_short"],
        "ma_mid": ma_info["ma_mid"],
        "ma_long": ma_info["ma_long"],
        "ma_aligned": ma_info["aligned"],
        "dist_to_short_pct": ma_info["dist_to_short_pct"],
        "drawdown_pct": drawdown,
        "atr_shrinking": atr_shrinking,
        "volume_ratio": vol_ratio,
        "close": close,
    }

    # 목표가 정보 추가
    if target_info:
        indicators["target_price"] = target_info.get("target_price", 0)
        indicators["upside_pct"] = target_info.get("upside_pct", 0)
        indicators["analyst_count"] = target_info.get("analyst_count", 0)
        indicators["consensus"] = target_info.get("consensus", "")

    result["indicators"] = indicators
    details: list[str] = []

    # ── 1) 목표가 점수 (0~35) ─────────────────────────────

    target_score = 0

    if target_info and target_info.get("target_price", 0) > 0:
        upside = target_info.get("upside_pct", 0)
        analyst_count = target_info.get("analyst_count", 0)

        # 상승여력 10~60% 구간만 유효
        if 10 <= upside <= 60:
            # 15~40% 이상적 구간 → 만점
            if 15 <= upside <= 40:
                target_score += 25
                details.append(f"목표가 상승여력 {upside:.1f}% (이상적 구간): +25")
            else:
                # 10~15% 또는 40~60% → 비례 점수
                target_score += 15
                details.append(f"목표가 상승여력 {upside:.1f}% (유효 구간): +15")

            # 증권사 수 보너스 (3개 이상이면 신뢰도 높음)
            if analyst_count >= 3:
                target_score += 10
                details.append(f"분석 증권사 {analyst_count}개 (≥3): +10")
            elif analyst_count >= 1:
                target_score += 5
                details.append(f"분석 증권사 {analyst_count}개: +5")
        elif upside > 0:
            details.append(f"목표가 상승여력 {upside:.1f}% (범위 밖)")
        else:
            details.append(f"목표가 이미 도달 또는 하회 ({upside:.1f}%)")
    else:
        details.append("목표가 정보 없음")

    result["target_score"] = min(target_score, 35)

    # ── 2) 순항도 점수 (0~40) ─────────────────────────────

    cruise_score = 0

    # 2a) 선형회귀 R² (0~15점) — 추세 일관성
    if slope_positive and r2 >= 0.6:
        if r2 >= 0.8:
            cruise_score += 15
            details.append(f"R² {r2:.2f} (≥0.8, 강한 추세): +15")
        else:
            cruise_score += 10
            details.append(f"R² {r2:.2f} (≥0.6, 양호한 추세): +10")
    elif slope_positive and r2 >= 0.4:
        cruise_score += 5
        details.append(f"R² {r2:.2f} (≥0.4, 약한 추세): +5")
    else:
        reason = "하락 추세" if not slope_positive else f"R² {r2:.2f} 낮음"
        details.append(f"추세 일관성 미달 ({reason})")

    # 2b) 최근 고점 대비 낙폭 (0~10점)
    if drawdown >= -3.0:  # 고점 대비 3% 이내
        cruise_score += 10
        details.append(f"고점 대비 {drawdown:.1f}% (3% 이내): +10")
    elif drawdown >= -5.0:  # 5% 이내
        cruise_score += 6
        details.append(f"고점 대비 {drawdown:.1f}% (5% 이내): +6")
    elif drawdown >= -8.0:
        cruise_score += 3
        details.append(f"고점 대비 {drawdown:.1f}% (8% 이내): +3")
    else:
        details.append(f"고점 대비 {drawdown:.1f}% (낙폭 과대)")

    # 2c) 이평선 정배열 (0~10점)
    if ma_info["aligned"]:
        cruise_score += 10
        details.append(
            f"이평 정배열 (MA5>{ma_info['ma_short']:,.0f} > MA20>{ma_info['ma_mid']:,.0f} "
            f"> MA60>{ma_info['ma_long']:,.0f}): +10"
        )
    else:
        # 부분 정배열 (MA5 > MA20만이라도)
        if ma_info["ma_short"] > ma_info["ma_mid"] and close > ma_info["ma_short"]:
            cruise_score += 5
            details.append("부분 정배열 (현재가>MA5>MA20): +5")
        else:
            details.append("이평 정배열 아님")

    # 2d) ATR 축소 추세 (0~5점)
    if atr_shrinking:
        cruise_score += 5
        details.append("ATR 축소 추세 (변동성 안정): +5")
    else:
        details.append("ATR 축소 없음")

    result["cruise_score"] = min(cruise_score, 40)

    # ── 3) 수급 점수 (0~15) ───────────────────────────────

    supply_score = 0

    if investor_data and len(investor_data) >= 3:
        # 최근 10일 중 외국인/기관 순매수 일수
        lookback = min(len(investor_data), 10)
        recent_inv = investor_data[:lookback]

        foreign_buy_days = sum(1 for d in recent_inv if d.get("foreign_net", 0) > 0)
        inst_buy_days = sum(1 for d in recent_inv if d.get("inst_net", 0) > 0)

        foreign_ratio = foreign_buy_days / lookback
        inst_ratio = inst_buy_days / lookback

        # 외국인 순매수 일수 비율 (0~8점)
        if foreign_ratio >= 0.7:
            supply_score += 8
            details.append(f"외국인 순매수 {foreign_buy_days}/{lookback}일 (≥70%): +8")
        elif foreign_ratio >= 0.5:
            supply_score += 5
            details.append(f"외국인 순매수 {foreign_buy_days}/{lookback}일 (≥50%): +5")
        elif foreign_ratio >= 0.3:
            supply_score += 2
            details.append(f"외국인 순매수 {foreign_buy_days}/{lookback}일: +2")

        # 기관 순매수 일수 비율 (0~7점)
        if inst_ratio >= 0.7:
            supply_score += 7
            details.append(f"기관 순매수 {inst_buy_days}/{lookback}일 (≥70%): +7")
        elif inst_ratio >= 0.5:
            supply_score += 4
            details.append(f"기관 순매수 {inst_buy_days}/{lookback}일 (≥50%): +4")
        elif inst_ratio >= 0.3:
            supply_score += 2
            details.append(f"기관 순매수 {inst_buy_days}/{lookback}일: +2")

        indicators["foreign_buy_days"] = foreign_buy_days
        indicators["inst_buy_days"] = inst_buy_days
        indicators["supply_lookback"] = lookback
    else:
        details.append("수급 데이터 부족")

    result["supply_score"] = min(supply_score, 15)

    # ── 4) 거래량 점수 (0~10) ─────────────────────────────

    volume_score = 0

    # 적정 거래량: 평균 대비 0.8~2.5배 (너무 적지도 많지도 않은)
    if 0.8 <= vol_ratio <= 2.5:
        volume_score += 7
        details.append(f"적정 거래량 (평균 대비 {vol_ratio:.1f}배): +7")
    elif 0.5 <= vol_ratio <= 4.0:
        volume_score += 3
        details.append(f"거래량 허용 범위 (평균 대비 {vol_ratio:.1f}배): +3")
    else:
        details.append(f"거래량 비정상 (평균 대비 {vol_ratio:.1f}배)")

    # 거래량 안정성 보너스: 최근 5일 거래량 표준편차가 낮으면
    if len(volumes) >= 5:
        recent_vols = volumes[-5:]
        avg_vol = sum(recent_vols) / 5
        if avg_vol > 0:
            vol_cv = (sum((v - avg_vol) ** 2 for v in recent_vols) / 5) ** 0.5 / avg_vol
            if vol_cv < 0.5:
                volume_score += 3
                details.append(f"거래량 안정 (CV {vol_cv:.2f} < 0.5): +3")

    result["volume_score"] = min(volume_score, 10)

    # ── 합산 ───────────────────────────────────────────────

    total = (
        result["target_score"]
        + result["cruise_score"]
        + result["supply_score"]
        + result["volume_score"]
    )
    result["total_score"] = min(total, 100)
    result["details"] = details

    logger.debug(
        "스코어 %s: 총 %d (목표 %d + 순항 %d + 수급 %d + 거래량 %d)",
        code, result["total_score"],
        result["target_score"], result["cruise_score"],
        result["supply_score"], result["volume_score"],
    )

    return result
