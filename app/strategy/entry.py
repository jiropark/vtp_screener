"""진입 평가 모듈 — 눌림목 진입 전략.

목표가 순항 종목이 단기 이평선에 눌림목을 형성할 때 매수.

시나리오:
  A: 현재가가 MA5 근처 (±2%) + 정배열  → BUY_PULLBACK (눌림목 매수)
  B: 현재가가 MA5 위 2~5%              → WAIT (조정 대기)
  C: 현재가가 MA5 아래 2% 이상         → SKIP (추세 이탈 가능성)
  D: 이평 역배열                       → INVALIDATE (추세 부정)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def evaluate_entry(
    signal: dict,
    current_price: int,
    prev_close: int,
    atr: float,
    ma_info: Optional[dict] = None,
) -> dict:
    """진입 시그널을 평가하여 매수 액션을 결정한다.

    Parameters
    ----------
    signal : dict
        스코어링 결과. code, total_score 등 포함.
    current_price : int
        현재가.
    prev_close : int
        전일 종가.
    atr : float
        14일 ATR.
    ma_info : dict, optional
        이평선 정보. {ma_short, ma_mid, ma_long, aligned, dist_to_short_pct}

    Returns
    -------
    dict
        action: "BUY_PULLBACK" | "WAIT" | "SKIP" | "INVALIDATE"
        scenario: "A" | "B" | "C" | "D"
        target_price: 매수 목표가
        reason: 판단 사유
    """
    result = {
        "code": signal.get("code", ""),
        "action": "SKIP",
        "scenario": "C",
        "gap_pct": 0.0,
        "gap_atr_ratio": 0.0,
        "target_price": current_price,
        "reason": "",
    }

    if prev_close <= 0 or current_price <= 0:
        result["action"] = "INVALIDATE"
        result["scenario"] = "D"
        result["reason"] = "유효하지 않은 가격"
        return result

    # 등락률 계산
    change_pct = (current_price - prev_close) / prev_close * 100
    result["gap_pct"] = round(change_pct, 2)

    # ATR 비율
    atr_pct = (atr / prev_close) * 100 if prev_close > 0 else 0
    result["gap_atr_ratio"] = round(change_pct / atr_pct, 2) if atr_pct > 0 else 0

    # 이평선 정보 없으면 기본 판단
    if not ma_info:
        result["action"] = "SKIP"
        result["scenario"] = "C"
        result["reason"] = "이평선 정보 없음 → 판단 보류"
        return result

    ma_short = ma_info.get("ma_short", 0)
    aligned = ma_info.get("aligned", False)
    dist_pct = ma_info.get("dist_to_short_pct", 0)

    # ── 시나리오 D: 역배열 → 무효화 ──
    # 현재가 < MA20 또는 MA5 < MA20이면 추세 부정
    ma_mid = ma_info.get("ma_mid", 0)
    if ma_mid > 0 and current_price < ma_mid:
        result["action"] = "INVALIDATE"
        result["scenario"] = "D"
        result["reason"] = f"현재가({current_price:,}) < MA20({ma_mid:,.0f}) → 추세 이탈"
        logger.info("[ENTRY-D] %s 추세 이탈 → INVALIDATE", signal.get("code"))
        return result

    # ── 시나리오 A: MA5 근처 (±2%) + 부분/완전 정배열 → 눌림목 매수 ──
    if ma_short > 0 and -2.0 <= dist_pct <= 2.0:
        # 최소한 MA5 > MA20 이어야 함
        if ma_short > ma_mid:
            result["action"] = "BUY_PULLBACK"
            result["scenario"] = "A"
            result["target_price"] = current_price
            align_str = "정배열" if aligned else "부분정배열"
            result["reason"] = (
                f"MA5 근처 ({dist_pct:+.1f}%), {align_str}"
                f" → 눌림목 매수 @ {current_price:,}원"
            )
            logger.info(
                "[ENTRY-A] %s MA5거리 %+.1f%% (%s) → BUY_PULLBACK @ %s원",
                signal.get("code"), dist_pct, align_str, f"{current_price:,}",
            )
            return result

    # ── 시나리오 B: MA5 위 2~5% → 조정 대기 ──
    if 2.0 < dist_pct <= 5.0 and ma_short > ma_mid:
        result["action"] = "WAIT"
        result["scenario"] = "B"
        result["target_price"] = int(ma_short)  # MA5까지 눌림 대기
        result["reason"] = (
            f"MA5 대비 +{dist_pct:.1f}% 이격"
            f" → MA5({ma_short:,.0f}원) 근처 조정 대기"
        )
        logger.info(
            "[ENTRY-B] %s MA5 대비 +%.1f%% → WAIT (조정 대기)",
            signal.get("code"), dist_pct,
        )
        return result

    # ── 시나리오 C: 그 외 → 스킵 ──
    result["action"] = "SKIP"
    result["scenario"] = "C"
    if dist_pct < -2.0:
        result["reason"] = f"MA5 아래 {dist_pct:.1f}% → 추세 약화 스킵"
    elif dist_pct > 5.0:
        result["reason"] = f"MA5 대비 +{dist_pct:.1f}% 과이격 → 스킵"
    else:
        result["reason"] = f"진입 조건 미충족 (MA5 거리 {dist_pct:+.1f}%)"

    logger.info(
        "[ENTRY-C] %s → SKIP (%s)",
        signal.get("code"), result["reason"],
    )
    return result
