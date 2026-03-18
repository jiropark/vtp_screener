"""리스크 매니저 — 일일/주간 손실 한도 및 연속 손절 관리.

체크 항목:
  - 일일 손실 -2% 초과 → 당일 매매 중단
  - 주간 손실 -5% 초과 → 주간 매매 중단
  - 연속 3회 손절 → 매매 중단 (쿨오프)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 한도 기본값
DAILY_LOSS_LIMIT_PCT = -2.0     # 일일 -2%
WEEKLY_LOSS_LIMIT_PCT = -5.0    # 주간 -5%
MAX_CONSECUTIVE_LOSSES = 3       # 연속 손절 횟수


class RiskManager:
    """리스크 관리 (일일/주간 손실한도, 연속 손절 체크)."""

    _instance: Optional["RiskManager"] = None

    def __init__(self) -> None:
        # 일일 상태
        self._daily_pnl_pct: float = 0.0       # 일일 누적 손익 (%)
        self._daily_trades: int = 0             # 일일 매매 횟수
        self._daily_date: str = ""              # 현재 추적 일자

        # 주간 상태
        self._weekly_pnl_pct: float = 0.0       # 주간 누적 손익 (%)
        self._weekly_start_date: str = ""       # 주간 시작일

        # 연속 손절
        self._consecutive_losses: int = 0       # 연속 손절 횟수
        self._last_trade_result: str = ""       # "WIN" | "LOSS"

        # 잠금 상태
        self._daily_locked: bool = False
        self._weekly_locked: bool = False
        self._consec_locked: bool = False

        self._ensure_daily()
        self._ensure_weekly()

    # ── 싱글턴 ─────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "RiskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """테스트용."""
        cls._instance = None

    # ── 날짜 초기화 ────────────────────────────────────────

    def _ensure_daily(self) -> None:
        """일자가 바뀌면 일일 상태 리셋."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_date != today:
            self.reset_daily()
            self._daily_date = today

    def _ensure_weekly(self) -> None:
        """월요일이 바뀌면 주간 상태 리셋."""
        now = datetime.now()
        # 이번 주 월요일
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        if self._weekly_start_date != monday:
            self.reset_weekly()
            self._weekly_start_date = monday

    # ── 한도 체크 ──────────────────────────────────────────

    def check_daily_limit(self) -> bool:
        """일일 -2% 초과 여부. True면 한도 초과(매매 불가)."""
        self._ensure_daily()
        exceeded = self._daily_pnl_pct <= DAILY_LOSS_LIMIT_PCT
        if exceeded and not self._daily_locked:
            self._daily_locked = True
            logger.warning(
                "일일 손실 한도 초과: %.2f%% <= %.2f%%",
                self._daily_pnl_pct, DAILY_LOSS_LIMIT_PCT,
            )
        return exceeded

    def check_weekly_limit(self) -> bool:
        """주간 -5% 초과 여부. True면 한도 초과(매매 불가)."""
        self._ensure_weekly()
        exceeded = self._weekly_pnl_pct <= WEEKLY_LOSS_LIMIT_PCT
        if exceeded and not self._weekly_locked:
            self._weekly_locked = True
            logger.warning(
                "주간 손실 한도 초과: %.2f%% <= %.2f%%",
                self._weekly_pnl_pct, WEEKLY_LOSS_LIMIT_PCT,
            )
        return exceeded

    def check_consecutive_losses(self) -> bool:
        """연속 3회 손절 여부. True면 매매 불가."""
        exceeded = self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES
        if exceeded and not self._consec_locked:
            self._consec_locked = True
            logger.warning(
                "연속 손절 %d회 → 매매 중단", self._consecutive_losses,
            )
        return exceeded

    def is_trading_allowed(self) -> bool:
        """모든 리스크 체크를 통과하면 True (매매 가능)."""
        self._ensure_daily()
        self._ensure_weekly()

        if self.check_daily_limit():
            return False
        if self.check_weekly_limit():
            return False
        if self.check_consecutive_losses():
            return False

        return True

    # ── 결과 기록 ──────────────────────────────────────────

    def record_loss(self, loss_pct: float) -> None:
        """손절 결과 기록.

        Parameters
        ----------
        loss_pct : float
            손실률 (음수, 예: -1.5).
        """
        self._ensure_daily()
        self._ensure_weekly()

        self._daily_pnl_pct += loss_pct
        self._weekly_pnl_pct += loss_pct
        self._daily_trades += 1

        self._consecutive_losses += 1
        self._last_trade_result = "LOSS"

        logger.info(
            "손절 기록: %.2f%% | 일일 누적 %.2f%%, 주간 누적 %.2f%%, 연속 %d회",
            loss_pct, self._daily_pnl_pct, self._weekly_pnl_pct,
            self._consecutive_losses,
        )

    def record_win(self, win_pct: float) -> None:
        """익절/수익 결과 기록.

        Parameters
        ----------
        win_pct : float
            수익률 (양수, 예: 2.5).
        """
        self._ensure_daily()
        self._ensure_weekly()

        self._daily_pnl_pct += win_pct
        self._weekly_pnl_pct += win_pct
        self._daily_trades += 1

        # 연속 손절 카운터 리셋
        self._consecutive_losses = 0
        self._consec_locked = False
        self._last_trade_result = "WIN"

        logger.info(
            "수익 기록: +%.2f%% | 일일 누적 %.2f%%, 주간 누적 %.2f%%",
            win_pct, self._daily_pnl_pct, self._weekly_pnl_pct,
        )

    # ── 리셋 ──────────────────────────────────────────────

    def reset_daily(self) -> None:
        """일일 상태 리셋 (매일 장 시작 시)."""
        self._daily_pnl_pct = 0.0
        self._daily_trades = 0
        self._daily_locked = False
        logger.info("일일 리스크 리셋")

    def reset_weekly(self) -> None:
        """주간 상태 리셋 (매주 월요일)."""
        self._weekly_pnl_pct = 0.0
        self._weekly_locked = False
        logger.info("주간 리스크 리셋")

    # ── 상태 조회 ──────────────────────────────────────────

    def get_status(self) -> dict:
        """현재 리스크 상태 요약."""
        self._ensure_daily()
        self._ensure_weekly()

        return {
            "trading_allowed": self.is_trading_allowed(),
            "daily_pnl_pct": round(self._daily_pnl_pct, 2),
            "daily_limit": DAILY_LOSS_LIMIT_PCT,
            "daily_locked": self._daily_locked,
            "daily_trades": self._daily_trades,
            "weekly_pnl_pct": round(self._weekly_pnl_pct, 2),
            "weekly_limit": WEEKLY_LOSS_LIMIT_PCT,
            "weekly_locked": self._weekly_locked,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive": MAX_CONSECUTIVE_LOSSES,
            "consec_locked": self._consec_locked,
            "last_trade_result": self._last_trade_result,
        }
