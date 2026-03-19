"""VTP 가상 포트폴리오 관리 (Singleton).

매수/매도/부분매도, 쿨다운, 포지션 조회, 총자산 계산.
DB 함수는 app.storage.db의 실제 시그니처에 맞춰 호출한다.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.config import (
    BUY_FEE_RATE,
    COOLDOWN_MINUTES,
    INITIAL_CAPITAL,
    MAX_POSITIONS,
    MIN_CASH_RATIO,
    POSITION_SIZE,
    SELL_FEE_RATE,
    SELL_TAX_RATE,
)
from app.storage.db import (
    delete_position,
    get_positions,
    get_position as db_get_position,
    save_position as db_save_position,
    save_trade as db_save_trade,
    update_position as db_update_position,
    _conn,
)

logger = logging.getLogger(__name__)


class Portfolio:
    """VTP 가상 매매 포트폴리오 (싱글턴)."""

    _instance: Optional["Portfolio"] = None

    def __init__(self) -> None:
        self._positions: list[dict] = []
        self._total_bought: int = 0
        self._sell_total: int = 0
        self._fees_total: int = 0
        self._reload()

    # ── 싱글턴 ─────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "Portfolio":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """테스트용 인스턴스 리셋."""
        cls._instance = None

    # ── 상태 로드 ──────────────────────────────────────────

    def _reload(self) -> None:
        """DB에서 포지션과 거래 내역을 로드해 상태를 재구성."""
        try:
            self._positions = get_positions()
        except Exception:
            logger.exception("포지션 로드 실패")
            self._positions = []

        self._recalc_cash_state()
        logger.info(
            "포트폴리오 로드: 포지션 %d개, 현금 %s원",
            len(self._positions),
            f"{self.cash:,}",
        )

    def _recalc_cash_state(self) -> None:
        """DB 기반 현금 상태 재계산."""
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM trades WHERE side = 'BUY'"
                ).fetchone()
                self._total_bought = row[0] if row else 0

                row = c.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM trades WHERE side = 'SELL'"
                ).fetchone()
                self._sell_total = row[0] if row else 0
        except Exception:
            logger.exception("현금 상태 계산 실패")
            self._total_bought = sum(
                p.get("buy_price", 0) * p.get("quantity", 0)
                for p in self._positions
            )
            self._sell_total = 0

        self._fees_total = (
            int(self._total_bought * BUY_FEE_RATE)
            + int(self._sell_total * (SELL_FEE_RATE + SELL_TAX_RATE))
        )

    # ── 프로퍼티 ───────────────────────────────────────────

    @property
    def cash(self) -> int:
        """현금 = 초기자본 - 누적매수 + 누적매도 - 누적수수료."""
        return INITIAL_CAPITAL - self._total_bought + self._sell_total - self._fees_total

    @property
    def positions(self) -> list[dict]:
        return list(self._positions)

    # ── 매수 ───────────────────────────────────────────────

    def buy(
        self,
        code: str,
        name: str,
        price: int,
        quantity: int,
        score: float,
        atr: float,
    ) -> bool:
        """가상 매수 실행.

        Parameters
        ----------
        code : str
            종목코드.
        name : str
            종목명.
        price : int
            매수 단가.
        quantity : int
            매수 수량.
        score : float
            스코어링 점수.
        atr : float
            진입 시점 ATR (손절가 산출용).

        Returns
        -------
        bool
            매수 성공 여부.
        """
        if not self.can_buy():
            logger.warning("매수 불가: 포지션 %d/%d, 현금 %s원",
                           len(self._positions), MAX_POSITIONS, f"{self.cash:,}")
            return False

        # 중복 보유 방지
        if any(p["code"] == code for p in self._positions):
            logger.warning("이미 보유 중: %s %s", code, name)
            return False

        # 쿨다운 체크
        if self.is_in_cooldown(code):
            logger.info("쿨다운 중: %s (%d분)", code, COOLDOWN_MINUTES)
            return False

        if quantity <= 0 or price <= 0:
            logger.warning("잘못된 매수 파라미터: price=%d, qty=%d", price, quantity)
            return False

        amount = price * quantity
        buy_fee = int(amount * BUY_FEE_RATE)

        # DB 저장
        try:
            db_save_position(
                code, name, price, quantity,
                atr_at_entry=atr, entry_score=score,
            )
            db_save_trade(
                code, name, "BUY", price, quantity, amount,
                fee=buy_fee, score=score, reason="VTP진입",
            )
        except Exception:
            logger.exception("매수 DB 저장 실패: %s", code)
            return False

        # 내부 상태 갱신
        self._total_bought += amount
        self._fees_total += buy_fee
        self._positions.append({
            "code": code,
            "name": name,
            "buy_price": price,
            "quantity": quantity,
            "original_quantity": quantity,
            "highest_price": price,
            "atr_at_entry": atr,
            "entry_score": score,
            "entry_time": datetime.now().isoformat(),
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "partial_sold": False,
        })

        logger.info(
            "[BUY] %s %s | %s원 x %d주 = %s원 (score %.0f, ATR %.0f) | 잔여현금 %s원",
            code, name, f"{price:,}", quantity, f"{amount:,}",
            score, atr, f"{self.cash:,}",
        )

        try:
            from app.notifier import notify_buy
            notify_buy(code, name, price, quantity, score=score)
        except Exception:
            logger.debug("매수 알림 전송 실패")

        return True

    # ── 매도 (전량) ────────────────────────────────────────

    def sell(self, code: str, price: int, quantity: int, reason: str) -> dict:
        """전량 매도.

        Returns
        -------
        dict
            pnl 정보. 실패 시 빈 dict.
        """
        pos = self.get_position(code)
        if pos is None:
            logger.warning("보유하지 않은 종목 매도 시도: %s", code)
            return {}

        sell_qty = min(quantity, pos["quantity"])
        return self._execute_sell(pos, price, sell_qty, reason)

    # ── 부분 매도 ──────────────────────────────────────────

    def partial_sell(self, code: str, price: int, ratio: float = 0.5) -> dict:
        """비율 기반 부분 매도.

        Returns
        -------
        dict
            pnl 정보. 실패 시 빈 dict.
        """
        pos = self.get_position(code)
        if pos is None:
            logger.warning("보유하지 않은 종목 부분매도 시도: %s", code)
            return {}

        sell_qty = max(1, int(pos["quantity"] * ratio))
        if sell_qty >= pos["quantity"]:
            sell_qty = pos["quantity"]

        return self._execute_sell(pos, price, sell_qty, f"부분매도({ratio*100:.0f}%)")

    def _execute_sell(self, pos: dict, price: int, sell_qty: int, reason: str) -> dict:
        """실제 매도 처리 공통 로직."""
        code = pos["code"]
        name = pos.get("name", "")
        buy_price = pos["buy_price"]

        sell_amount = price * sell_qty
        sell_fee = int(sell_amount * SELL_FEE_RATE)
        sell_tax = int(sell_amount * SELL_TAX_RATE)
        total_sell_cost = sell_fee + sell_tax

        # 순수익 계산
        buy_fee_per = int(buy_price * BUY_FEE_RATE)
        sell_cost_per = int(price * (SELL_FEE_RATE + SELL_TAX_RATE))
        net_profit_per = price - buy_price - buy_fee_per - sell_cost_per
        profit_amount = net_profit_per * sell_qty
        profit_pct = (
            (net_profit_per / (buy_price + buy_fee_per)) * 100
            if buy_price else 0
        )

        is_full_sell = sell_qty >= pos["quantity"]

        # DB 저장
        try:
            db_save_trade(
                code, name, "SELL", price, sell_qty, sell_amount,
                fee=sell_fee, tax=sell_tax,
                reason=reason,
                pnl=profit_amount,
                pnl_pct=round(profit_pct, 2),
            )

            if is_full_sell:
                delete_position(code)
                self._positions = [p for p in self._positions if p["code"] != code]
            else:
                remaining = pos["quantity"] - sell_qty
                db_update_position(code, quantity=remaining, partial_sold=1)
                for p in self._positions:
                    if p["code"] == code:
                        p["quantity"] = remaining
                        p["partial_sold"] = True
                        break
        except Exception:
            logger.exception("매도 DB 저장 실패: %s", code)
            return {}

        self._sell_total += sell_amount
        self._fees_total += total_sell_cost

        label = "SELL" if is_full_sell else "PARTIAL_SELL"
        logger.info(
            "[%s] %s %s | %s원 x %d주 = %s원 | 수익 %+.2f%% (%s원) | 사유: %s",
            label, code, name,
            f"{price:,}", sell_qty, f"{sell_amount:,}",
            profit_pct, f"{profit_amount:+,}", reason,
        )

        result = {
            "code": code,
            "name": name,
            "side": label,
            "price": price,
            "quantity": sell_qty,
            "amount": sell_amount,
            "profit_pct": round(profit_pct, 2),
            "profit_amount": profit_amount,
            "reason": reason,
        }

        try:
            from app.notifier import notify_sell
            notify_sell(code, name, price, sell_qty,
                        pnl_pct=round(profit_pct, 2), reason=reason)
        except Exception:
            logger.debug("매도 알림 전송 실패")

        return result

    # ── 조회 ───────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """현재 보유 포지션 목록."""
        return list(self._positions)

    def get_position(self, code: str) -> Optional[dict]:
        """특정 종목 포지션 조회."""
        for p in self._positions:
            if p["code"] == code:
                return p
        return None

    # ── 현재가 갱신 ────────────────────────────────────────

    def update_prices(self) -> None:
        """보유 종목 현재가 조회 → highest_price 갱신."""
        try:
            from app.api.rest import get_current_price
        except ImportError:
            logger.debug("get_current_price 미구현")
            return

        for pos in self._positions:
            try:
                data = get_current_price(pos["code"])
                cur = data.get("price", 0) if data else 0
                if cur and cur > pos.get("highest_price", 0):
                    pos["highest_price"] = cur
                    db_update_position(pos["code"], highest_price=cur)
                    logger.debug("최고가 갱신: %s → %s원", pos["code"], f"{cur:,}")
            except Exception:
                logger.debug("가격 조회 실패: %s", pos["code"])

    # ── 총자산 ─────────────────────────────────────────────

    def calc_total_asset(self) -> dict:
        """현금, 주식평가, 총자산을 계산.

        Returns
        -------
        dict
            cash, stock_value, total, profit_pct.
        """
        stock_value = 0
        for pos in self._positions:
            cur = pos.get("highest_price", pos["buy_price"])
            stock_value += cur * pos["quantity"]

        total = self.cash + stock_value
        profit_pct = (total - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

        return {
            "cash": self.cash,
            "stock_value": stock_value,
            "total": total,
            "profit_pct": round(profit_pct, 2),
            "initial_capital": INITIAL_CAPITAL,
        }

    # ── 매수 가능 여부 ─────────────────────────────────────

    def can_buy(self) -> bool:
        """매수 가능 여부: 포지션 수, 현금 비율 체크."""
        if len(self._positions) >= MAX_POSITIONS:
            return False

        stock_est = sum(p["buy_price"] * p["quantity"] for p in self._positions)
        total_est = self.cash + stock_est
        if total_est <= 0:
            return False

        min_cash = int(total_est * MIN_CASH_RATIO)
        # 1 포지션분(POSITION_SIZE) + 최소현금 이상 보유해야 매수 가능
        return self.cash > min_cash + POSITION_SIZE

    # ── 쿨다운 ─────────────────────────────────────────────

    def is_in_cooldown(self, code: str) -> bool:
        """매도 후 COOLDOWN_MINUTES 이내 재매수 방지."""
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT COUNT(*) FROM trades WHERE code = ? AND side = 'SELL' "
                    "AND created_at >= datetime('now', 'localtime', ?)",
                    (code, f"-{COOLDOWN_MINUTES} minutes"),
                ).fetchone()
                return row[0] > 0
        except Exception:
            return False

    # ── 내부 현금 계산 (검증용) ─────────────────────────────

    def _calc_cash(self) -> int:
        """trades 이력 기반 현금 재계산."""
        try:
            with _conn() as c:
                buy_row = c.execute(
                    "SELECT COALESCE(SUM(amount + fee), 0) FROM trades WHERE side = 'BUY'"
                ).fetchone()
                sell_row = c.execute(
                    "SELECT COALESCE(SUM(amount - fee - tax), 0) FROM trades WHERE side = 'SELL'"
                ).fetchone()

            total_bought = buy_row[0] if buy_row else 0
            total_sold = sell_row[0] if sell_row else 0
            return INITIAL_CAPITAL - total_bought + total_sold
        except Exception:
            logger.exception("현금 재계산 실패")
            return self.cash
