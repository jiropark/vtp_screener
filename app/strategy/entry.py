"""진입 평가 모듈 — 갭 + ATR 기반 시나리오 판단.

시나리오:
  A: gap_atr <= 0.8   → BUY_MARKET (시장가 즉시 매수)
  B: 0.8 < gap_atr <= 1.5 → BUY_VWAP_PULLBACK (VWAP 눌림 대기)
  C: gap_atr > 1.5    → SKIP (과열, 진입 보류)
  D: gap < 0          → INVALIDATE (음봉 갭다운, 무효화)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 시나리오 경계값
GAP_ATR_THRESHOLD_A = 0.8   # A/B 경계
GAP_ATR_THRESHOLD_B = 1.5   # B/C 경계


def evaluate_entry(
    signal: dict,
    current_price: int,
    prev_close: int,
    atr: float,
    vwap: float = 0.0,
) -> dict:
    """진입 시그널을 평가하여 매수 액션을 결정한다.

    Parameters
    ----------
    signal : dict
        스코어링 결과. code, total_score 등 포함.
    current_price : int
        현재가 (또는 시가).
    prev_close : int
        전일 종가.
    atr : float
        14일 ATR.
    vwap : float, optional
        당일 VWAP (시나리오 B 타겟가 산출용).

    Returns
    -------
    dict
        action: "BUY_MARKET" | "BUY_VWAP_PULLBACK" | "SKIP" | "INVALIDATE"
        scenario: "A" | "B" | "C" | "D"
        gap_pct: 갭 비율 (%)
        gap_atr_ratio: 갭 / ATR 비율
        target_price: 매수 목표가 (시나리오 B에서 VWAP)
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

    # 갭 계산
    gap = current_price - prev_close
    gap_pct = (gap / prev_close) * 100
    result["gap_pct"] = round(gap_pct, 2)

    # ATR 대비 갭 비율
    atr_pct = (atr / prev_close) if prev_close > 0 else 0
    gap_atr_ratio = (gap_pct / 100) / atr_pct if atr_pct > 0 else 0
    result["gap_atr_ratio"] = round(gap_atr_ratio, 2)

    # ── 시나리오 D: 갭 다운 (음봉) ──
    # 0.5% 이상 갭다운만 무효화, 소폭 갭다운(-0.5% 미만)은 시나리오 A로 진행
    if gap < 0 and abs(gap_pct) >= 0.5:
        result["action"] = "INVALIDATE"
        result["scenario"] = "D"
        result["reason"] = f"갭다운 {gap_pct:+.2f}% → 시그널 무효화"
        logger.info(
            "[ENTRY-D] %s 갭다운 %+.2f%% → INVALIDATE",
            signal.get("code"), gap_pct,
        )
        return result

    # ── 시나리오 A: 갭ATR <= 0.8 → 즉시 매수 ──
    if gap_atr_ratio <= GAP_ATR_THRESHOLD_A:
        result["action"] = "BUY_MARKET"
        result["scenario"] = "A"
        result["target_price"] = current_price
        result["reason"] = (
            f"갭 {gap_pct:+.2f}%, ATR비율 {gap_atr_ratio:.2f} <= {GAP_ATR_THRESHOLD_A}"
            f" → 시장가 매수"
        )
        logger.info(
            "[ENTRY-A] %s 갭 %+.2f%% (ATR비율 %.2f) → BUY_MARKET @ %s원",
            signal.get("code"), gap_pct, gap_atr_ratio, f"{current_price:,}",
        )
        return result

    # ── 시나리오 B: 0.8 < 갭ATR <= 1.5 → VWAP 눌림 대기 ──
    if gap_atr_ratio <= GAP_ATR_THRESHOLD_B:
        target = int(vwap) if vwap > 0 else current_price
        result["action"] = "BUY_VWAP_PULLBACK"
        result["scenario"] = "B"
        result["target_price"] = target
        result["reason"] = (
            f"갭 {gap_pct:+.2f}%, ATR비율 {gap_atr_ratio:.2f}"
            f" → VWAP({target:,}원) 눌림 대기"
        )
        logger.info(
            "[ENTRY-B] %s 갭 %+.2f%% (ATR비율 %.2f) → VWAP_PULLBACK target %s원",
            signal.get("code"), gap_pct, gap_atr_ratio, f"{target:,}",
        )
        return result

    # ── 시나리오 C: 갭ATR > 1.5 → 과열 스킵 ──
    result["action"] = "SKIP"
    result["scenario"] = "C"
    result["reason"] = (
        f"갭 {gap_pct:+.2f}%, ATR비율 {gap_atr_ratio:.2f} > {GAP_ATR_THRESHOLD_B}"
        f" → 과열 스킵"
    )
    logger.info(
        "[ENTRY-C] %s 갭 %+.2f%% (ATR비율 %.2f) → SKIP (과열)",
        signal.get("code"), gap_pct, gap_atr_ratio,
    )
    return result
