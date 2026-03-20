"""유니버스 필터링 — KOSPI/KOSDAQ 종목 중 스크리닝 대상 추출.

필터 조건:
  1. 시가총액 >= MIN_MARKET_CAP (1000억)
  2. 20일 평균 거래대금 >= 10억
  3. 관리종목/정리매매/상하한가 제외
  4. 대차잔고 급증 (20일 대비 50%↑) 제외
  5. 외국인+기관 동시 3일 연속 순매도 제외 (supply filter)
  6. 목표가 존재 및 최소 상승여력 10% 이상
"""

import logging
from typing import Any

from app.config import MIN_MARKET_CAP

logger = logging.getLogger(__name__)

# 20일 평균 거래대금 최소 기준 (10억원)
MIN_AVG_TRADE_AMOUNT_20D = 1_000_000_000

# 대차잔고 급증 판정 비율
LENDING_SURGE_RATIO = 0.50  # 20일 평균 대비 50% 이상 증가

# 수급 필터: 연속 순매도 일수
SUPPLY_CONSEC_SELL_DAYS = 3


def filter_universe(
    all_stocks: list[dict],
    ohlcv_fetcher=None,
    investor_fetcher=None,
    lending_fetcher=None,
    target_fetcher=None,
) -> list[dict]:
    """전체 종목 리스트에서 스크리닝 유니버스를 필터링한다.

    Parameters
    ----------
    all_stocks : list[dict]
        전체 종목. 각 dict에 최소 다음 키 포함:
        - code, name, market_cap, market (KOSPI/KOSDAQ)
        - is_managed (관리종목), is_clearing (정리매매)
        - upper_limit (상한가), lower_limit (하한가)
    ohlcv_fetcher : callable, optional
        code -> list[dict(date, open, high, low, close, volume, trade_amount)]
        최근 60일 이상 OHLCV 반환.
    investor_fetcher : callable, optional
        code -> list[dict(date, foreign_net, inst_net)]
        최근 5일 이상 투자자별 순매수 반환.
    lending_fetcher : callable, optional
        code -> dict(current, avg_20d) 대차잔고 정보 반환.
    target_fetcher : callable, optional
        code -> dict(target_price, upside_pct, ...) 목표가 정보 반환.

    Returns
    -------
    list[dict]
        필터 통과 종목 리스트. 원본 dict에 avg_trade_amount_20d 필드 추가.
    """
    passed: list[dict] = []
    excluded_counts: dict[str, int] = {
        "market_cap": 0,
        "managed_clearing": 0,
        "limit_price": 0,
        "trade_amount": 0,
        "lending_surge": 0,
        "supply_filter": 0,
        "no_target": 0,
    }

    for stock in all_stocks:
        code = stock.get("code", "")
        name = stock.get("name", "")

        # ── 1) 시가총액 필터 ──
        market_cap = stock.get("market_cap", 0)
        if market_cap < MIN_MARKET_CAP:
            excluded_counts["market_cap"] += 1
            continue

        # ── 2) 관리종목 / 정리매매 제외 ──
        if stock.get("is_managed") or stock.get("is_clearing"):
            excluded_counts["managed_clearing"] += 1
            continue

        # ── 3) 상한가 / 하한가 제외 ──
        if stock.get("upper_limit") or stock.get("lower_limit"):
            excluded_counts["limit_price"] += 1
            continue

        # ── 4) 20일 평균 거래대금 필터 ──
        avg_trade_amount = _calc_avg_trade_amount(code, stock, ohlcv_fetcher)
        if avg_trade_amount < MIN_AVG_TRADE_AMOUNT_20D:
            excluded_counts["trade_amount"] += 1
            continue

        # ── 5) 대차잔고 급증 필터 ──
        if _is_lending_surging(code, lending_fetcher):
            excluded_counts["lending_surge"] += 1
            logger.debug("대차잔고 급증 제외: %s %s", code, name)
            continue

        # ── 6) 외국인+기관 동시 3일 연속 순매도 (supply filter) ──
        if _is_supply_draining(code, investor_fetcher):
            excluded_counts["supply_filter"] += 1
            logger.debug("수급 악화 제외: %s %s", code, name)
            continue

        # ── 7) 목표가 존재 및 최소 상승여력 필터 ──
        target_info = _get_target_info(code, target_fetcher)
        if not target_info or target_info.get("upside_pct", 0) < 10:
            excluded_counts["no_target"] += 1
            continue

        # 필터 통과
        stock_copy = dict(stock)
        stock_copy["avg_trade_amount_20d"] = avg_trade_amount
        stock_copy["target_info"] = target_info
        passed.append(stock_copy)

    logger.info(
        "유니버스 필터링: %d/%d 통과 "
        "(시총 %d, 관리/정리 %d, 상하한 %d, 거래대금 %d, 대차 %d, 수급 %d, 목표가 %d 제외)",
        len(passed),
        len(all_stocks),
        excluded_counts["market_cap"],
        excluded_counts["managed_clearing"],
        excluded_counts["limit_price"],
        excluded_counts["trade_amount"],
        excluded_counts["lending_surge"],
        excluded_counts["supply_filter"],
        excluded_counts["no_target"],
    )

    return passed


# ── 내부 헬퍼 ─────────────────────────────────────────────────


def _get_target_info(code: str, target_fetcher) -> dict:
    """목표가 정보를 가져온다."""
    if target_fetcher is None:
        return {}
    try:
        return target_fetcher(code) or {}
    except Exception:
        logger.debug("목표가 조회 실패: %s", code)
        return {}


def _calc_avg_trade_amount(
    code: str,
    stock: dict,
    ohlcv_fetcher: Any,
) -> float:
    """20일 평균 거래대금을 계산한다.

    stock dict에 avg_trade_amount_20d가 이미 있으면 그대로 사용.
    없으면 ohlcv_fetcher를 호출해서 계산.
    """
    # 이미 계산된 값이 있는 경우 (외부에서 미리 넣어줌)
    pre = stock.get("avg_trade_amount_20d")
    if pre is not None:
        return pre

    if ohlcv_fetcher is None:
        return 0.0

    try:
        ohlcv = ohlcv_fetcher(code)
        if not ohlcv or len(ohlcv) < 5:
            return 0.0
        # 최근 20일 (데이터가 부족하면 있는 만큼)
        recent = ohlcv[-20:]
        amounts = [d.get("trade_amount", 0) for d in recent]
        return sum(amounts) / len(amounts) if amounts else 0.0
    except Exception:
        logger.debug("거래대금 조회 실패: %s", code)
        return 0.0


def _is_lending_surging(code: str, lending_fetcher: Any) -> bool:
    """대차잔고가 20일 평균 대비 50% 이상 급증했는지 판정."""
    if lending_fetcher is None:
        return False

    try:
        data = lending_fetcher(code)
        if not data:
            return False
        current = data.get("current", 0)
        avg_20d = data.get("avg_20d", 0)
        if avg_20d <= 0:
            return False
        increase_ratio = (current - avg_20d) / avg_20d
        return increase_ratio >= LENDING_SURGE_RATIO
    except Exception:
        logger.debug("대차잔고 조회 실패: %s", code)
        return False


def _is_supply_draining(code: str, investor_fetcher: Any) -> bool:
    """외국인 + 기관 동시 3일 연속 순매도인지 판정."""
    if investor_fetcher is None:
        return False

    try:
        data = investor_fetcher(code)
        if not data or len(data) < SUPPLY_CONSEC_SELL_DAYS:
            return False

        # 최근 3일 (날짜 내림차순 가정 → 앞 3개)
        recent = data[:SUPPLY_CONSEC_SELL_DAYS]

        for day in recent:
            foreign_net = day.get("foreign_net", 0)
            inst_net = day.get("inst_net", 0)
            # 둘 다 순매도(음수)여야 함
            if foreign_net >= 0 or inst_net >= 0:
                return False

        return True
    except Exception:
        logger.debug("투자자 데이터 조회 실패: %s", code)
        return False
