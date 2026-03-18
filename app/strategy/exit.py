"""3중 방어 엑싯 시스템 — 가격/구조/시간 기반 매도 판단.

판단 우선순위:
  1. 최대 보유일 초과 (3일)
  2. 가격 손절 (ATR 기반 스탑)
  3. 구조 이탈 (VWAP, 시초저가, 돌파캔들 저가)
  4. 시간 기반 (60분 고점 미갱신)
  5. 익절 (ATR 배수 기반 부분/전량)
  6. 트레일링 스탑 (최고가 - ATR*1.2)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.config import (
    ATR_STOP_MULTIPLIER,
    MAX_HOLD_DAYS,
)

logger = logging.getLogger(__name__)

# 손절 클램프 범위
STOP_LOSS_MIN_PCT = -0.015   # 최소 -1.5%
STOP_LOSS_MAX_PCT = -0.04    # 최대 -4%

# 익절 ATR 배수 경계
TAKE_PROFIT_PARTIAL_1 = 2.0  # 1차 부분매도 (50%)
TAKE_PROFIT_PARTIAL_2 = 3.0  # 2차 부분매도 (30%)

# 트레일링 ATR 배수
TRAILING_ATR_MULT = 1.2

# 시간 기반 판정 (분)
TIME_CHECK_MINUTES = 60


def check_exit(
    position: dict,
    current_price: int,
    vwap: float,
    minute_data: Optional[list[dict]] = None,
) -> dict:
    """포지션의 매도 여부를 3중 방어 기준으로 판단한다.

    Parameters
    ----------
    position : dict
        포지션 정보. 필수 키:
        - buy_price, quantity, highest_price, atr_at_entry
        - entry_time (ISO format), partial_sold (bool)
        - first_15min_low (시초 15분 저가, optional)
        - breakout_candle_low (돌파캔들 저가, optional)
    current_price : int
        현재가.
    vwap : float
        당일 VWAP.
    minute_data : list[dict], optional
        진입 후 분봉 데이터. 각 dict에 high, low, close, time.

    Returns
    -------
    dict
        action: "HOLD" | "SELL" | "PARTIAL_SELL"
        reason: 매도 사유 (한글)
        sell_ratio: 매도 비율 (PARTIAL_SELL 시)
        stop_price: 계산된 손절가
        profit_pct: 현재 수익률 (%)
    """
    buy_price = position.get("buy_price", 0)
    highest = position.get("highest_price", buy_price)
    atr = position.get("atr_at_entry", 0)
    partial_sold = position.get("partial_sold", False)

    if buy_price <= 0 or current_price <= 0:
        return _result("HOLD", "가격 정보 부족", buy_price=buy_price, current=current_price)

    profit_pct = (current_price - buy_price) / buy_price * 100

    # 손절가 계산 (ATR 기반 + 클램프)
    stop_price = _calc_stop_price(buy_price, atr)

    result_base = {
        "stop_price": stop_price,
        "profit_pct": round(profit_pct, 2),
        "highest_price": highest,
        "current_price": current_price,
    }

    # ── 1. 최대 보유일 초과 ────────────────────────────────
    entry_time = _parse_entry_time(position.get("entry_time"))
    if entry_time:
        hold_days = (datetime.now() - entry_time).days
        if hold_days >= MAX_HOLD_DAYS:
            return _result(
                "SELL", "최대 보유일 초과",
                sell_ratio=1.0, hold_days=hold_days, **result_base,
            )

    # ── 2. 가격 손절 ──────────────────────────────────────
    if current_price <= stop_price:
        return _result(
            "SELL", "가격 손절",
            sell_ratio=1.0, **result_base,
        )

    # ── 3. 구조 이탈 (Structure-based) ────────────────────

    # 3-a. VWAP 하향 이탈
    if vwap > 0 and current_price < vwap:
        return _result(
            "SELL", "VWAP 이탈",
            sell_ratio=1.0, vwap=vwap, **result_base,
        )

    # 3-b. 시초 15분 저가 이탈
    first_15min_low = position.get("first_15min_low")
    if first_15min_low and current_price < first_15min_low:
        return _result(
            "SELL", "시초 저가 이탈",
            sell_ratio=1.0, first_15min_low=first_15min_low, **result_base,
        )

    # 3-c. 돌파캔들 저가 이탈
    breakout_low = position.get("breakout_candle_low")
    if breakout_low and current_price < breakout_low:
        return _result(
            "SELL", "돌파캔들 이탈",
            sell_ratio=1.0, breakout_candle_low=breakout_low, **result_base,
        )

    # ── 4. 시간 기반 ──────────────────────────────────────
    if entry_time:
        minutes_held = (datetime.now() - entry_time).total_seconds() / 60

        if minutes_held >= TIME_CHECK_MINUTES:
            # 60분 동안 신고가 미갱신 체크
            new_high_since_entry = _check_new_high(minute_data, buy_price)

            if not new_high_since_entry and current_price < vwap:
                # 60분 + 고점 미갱신 + VWAP 아래 → 전량 매도
                return _result(
                    "SELL", "60분 VWAP 재이탈",
                    sell_ratio=1.0, minutes_held=int(minutes_held), **result_base,
                )

            if not new_high_since_entry and not partial_sold:
                # 60분 + 고점 미갱신 → 50% 부분매도
                return _result(
                    "PARTIAL_SELL", "60분 고점 미갱신",
                    sell_ratio=0.5, minutes_held=int(minutes_held), **result_base,
                )

    # ── 5. 익절 (Take-profit) ─────────────────────────────
    if atr > 0:
        profit_atr = (current_price - buy_price) / atr

        # 1차: 수익 >= 2 ATR → 50% 부분매도 (미실행 시)
        if profit_atr >= TAKE_PROFIT_PARTIAL_1 and not partial_sold:
            return _result(
                "PARTIAL_SELL", f"익절 1차 ({profit_atr:.1f} ATR)",
                sell_ratio=0.5, profit_atr=round(profit_atr, 2), **result_base,
            )

        # 2차: 수익 >= 3 ATR → 30% 추가 부분매도
        if profit_atr >= TAKE_PROFIT_PARTIAL_2:
            return _result(
                "PARTIAL_SELL", f"익절 2차 ({profit_atr:.1f} ATR)",
                sell_ratio=0.3, profit_atr=round(profit_atr, 2), **result_base,
            )

    # ── 6. 트레일링 스탑 ──────────────────────────────────
    if highest > buy_price and atr > 0:
        trailing_stop = highest - atr * TRAILING_ATR_MULT
        if current_price < trailing_stop:
            return _result(
                "SELL", "트레일링 스탑",
                sell_ratio=1.0, trailing_stop=int(trailing_stop), **result_base,
            )

    # ── HOLD ──────────────────────────────────────────────
    return _result("HOLD", "", **result_base)


# ── 내부 헬퍼 ─────────────────────────────────────────────────


def _calc_stop_price(buy_price: int, atr: float) -> int:
    """ATR 기반 손절가 계산 (클램프 적용).

    stop = buy_price - atr * ATR_STOP_MULTIPLIER
    단, -1.5% ~ -4% 범위로 클램프.
    """
    if atr <= 0 or buy_price <= 0:
        # ATR 없으면 -3% 기본 손절
        return int(buy_price * 0.97)

    raw_stop = buy_price - atr * ATR_STOP_MULTIPLIER
    min_stop = int(buy_price * (1 + STOP_LOSS_MAX_PCT))   # -4% (하한)
    max_stop = int(buy_price * (1 + STOP_LOSS_MIN_PCT))   # -1.5% (상한)

    # 클램프: stop_price는 min_stop ~ max_stop 사이
    clamped = int(max(min_stop, min(max_stop, raw_stop)))

    return clamped


def _parse_entry_time(entry_time_str: Optional[str]) -> Optional[datetime]:
    """entry_time 문자열 → datetime 파싱."""
    if not entry_time_str:
        return None
    try:
        return datetime.fromisoformat(entry_time_str)
    except (ValueError, TypeError):
        return None


def _check_new_high(minute_data: Optional[list[dict]], buy_price: int) -> bool:
    """진입 후 분봉에서 매입가 대비 신고가가 있었는지."""
    if not minute_data:
        return False

    for bar in minute_data:
        if bar.get("high", 0) > buy_price:
            return True

    return False


def _result(action: str, reason: str, sell_ratio: float = 0.0, **kwargs) -> dict:
    """엑싯 결과 dict 생성."""
    return {
        "action": action,
        "reason": reason,
        "sell_ratio": sell_ratio,
        **kwargs,
    }
