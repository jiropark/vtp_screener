"""VTP 스크리너 메인 엔트리포인트.

- 유니버스 필터링: 08:50 (전일 종가 기반)
- 갭 시나리오 진입: 09:00
- 장중 포지션 모니터링: 09:00~15:20, 1분 간격
- 종가 기반 스크리닝: 15:20
- 일일 스냅샷: 15:35
- 주간 리스크 리셋: 매주 월요일 08:00
- 웹 대시보드: Flask (포트 8092)
"""

import logging
import signal
import sys
import threading
from datetime import datetime, time as dt_time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import FLASK_PORT, load_dynamic_config
from app.storage.db import init_db, migrate_db

# ── 로깅 설정 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("/logs/vtp_screener.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

# ── 유니버스 캐시 (08:50 필터링 → 09:00 진입에서 사용) ──
_universe_cache: list[dict] = []


# ── 장 운영 시간 체크 ──

def is_market_open() -> bool:
    """한국 주식시장 운영 시간 확인 (평일 09:00~15:30)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 0) <= t <= dt_time(15, 30)


# ── 스케줄 작업 ──

def run_universe_filter():
    """유니버스 필터링 (거래량 상위 → 시가총액/거래대금 필터)."""
    global _universe_cache
    try:
        logger.info("── 유니버스 필터링 시작 ──")
        from app.api.rest import get_volume_rank, get_daily_ohlcv
        from app.screener.universe import filter_universe

        # 1. 거래량 상위 종목 조회 (네이버 API / KIS API)
        candidates = get_volume_rank()
        if not candidates:
            logger.warning("거래량 상위 종목 없음 → 유니버스 빈 상태")
            _universe_cache = []
            return

        logger.info("거래량 상위 후보: %d종목", len(candidates))

        # 2. 유니버스 필터 (시총, 거래대금, 관리종목 등)
        filtered = filter_universe(
            candidates,
            ohlcv_fetcher=get_daily_ohlcv,
        )
        _universe_cache = filtered
        logger.info("── 유니버스 필터링 완료: %d종목 통과 ──", len(filtered))

    except Exception:
        logger.exception("유니버스 필터링 중 예외 발생")
        _universe_cache = []


def run_signal_screening():
    """시그널 스크리닝 (전일 종가 기반 스코어링)."""
    try:
        logger.info("── 시그널 스크리닝 시작 ──")
        from app.api.rest import get_daily_ohlcv, get_investor_data
        from app.screener.scorer import score_stock
        from app.storage.db import save_signal, save_score_history
        from app.notifier import notify_signal
        from app.config import get_param, load_dynamic_config

        load_dynamic_config()  # 동적 설정 리로드
        threshold = get_param("SCORE_THRESHOLD", 60)
        universe = _universe_cache

        if not universe:
            logger.info("유니버스 비어있음 → 스크리닝 스킵")
            return

        signals_found = 0
        today_str = datetime.now().strftime("%Y-%m-%d")

        for stock in universe:
            code = stock["code"]
            name = stock.get("name", "")

            # 일봉 OHLCV (60일)
            ohlcv = get_daily_ohlcv(code, days=60)
            if not ohlcv or len(ohlcv) < 20:
                logger.debug("OHLCV 부족: %s %s (%d일)", code, name, len(ohlcv) if ohlcv else 0)
                continue

            # KIS API는 최신→과거 순 → 오름차순으로 뒤집기
            ohlcv.reverse()

            # 투자자 수급
            inv_data = get_investor_data(code)
            investor_list = [inv_data] if inv_data else None

            # 스코어링
            result = score_stock(code, ohlcv, investor_data=investor_list)
            total_score = result["total_score"]
            indicators = result.get("indicators", {})

            # 스코어 이력 저장
            save_score_history(
                today_str, code, name,
                total_score,
                result.get("volume_score", 0),
                result.get("price_score", 0),
                result.get("supply_bonus", 0),
                indicators.get("volume_ratio", 0),
                indicators.get("atr", 0),
                indicators.get("close_quality", 0),
            )

            # 임계값 이상이면 시그널 저장
            if total_score >= threshold:
                save_signal(
                    code, name, total_score,
                    volume_score=result.get("volume_score", 0),
                    price_score=result.get("price_score", 0),
                    supply_score=result.get("supply_bonus", 0),
                    volume_ratio=indicators.get("volume_ratio", 0),
                    atr=indicators.get("atr", 0),
                    status="DETECTED",
                )
                signals_found += 1

                # 텔레그램 알림
                notify_signal(code, name, total_score, {
                    "volume_score": result.get("volume_score", 0),
                    "price_score": result.get("price_score", 0),
                    "supply_score": result.get("supply_bonus", 0),
                    "volume_ratio": indicators.get("volume_ratio", 0),
                })

                logger.info(
                    "[SIGNAL] %s %s | 총점 %d (거래량 %d + 가격 %d + 수급 %d)",
                    code, name, total_score,
                    result.get("volume_score", 0),
                    result.get("price_score", 0),
                    result.get("supply_bonus", 0),
                )

        logger.info("── 시그널 스크리닝 완료: %d건 감지 ──", signals_found)

    except Exception:
        logger.exception("시그널 스크리닝 중 예외 발생")


def run_entry_check():
    """갭 시나리오 판단 + 매수 진입."""
    try:
        if not is_market_open():
            logger.info("장 미개장 → 진입 체크 스킵")
            return

        logger.info("── 갭 시나리오 진입 체크 시작 ──")
        from app.storage.db import get_today_signals, update_signal_status
        from app.api.rest import get_current_price, get_daily_ohlcv
        from app.screener.indicators import calc_atr, calc_vwap
        from app.strategy.entry import evaluate_entry
        from app.strategy.portfolio import Portfolio
        from app.strategy.risk import RiskManager
        from app.config import POSITION_SIZE

        risk = RiskManager.instance()
        if not risk.is_trading_allowed():
            logger.warning("리스크 한도 초과 → 진입 스킵")
            return

        portfolio = Portfolio.instance()
        if not portfolio.can_buy():
            logger.info("매수 불가 (포지션 수 또는 현금 부족)")
            return

        signals = get_today_signals()
        detected = [s for s in signals if s.get("status") == "DETECTED"]

        if not detected:
            logger.info("오늘 감지된 시그널 없음")
            return

        logger.info("진입 후보 시그널: %d건", len(detected))

        for sig in detected:
            code = sig["code"]
            name = sig.get("name", "")
            score = sig.get("score", 0)

            # 현재가 조회
            cur = get_current_price(code)
            if not cur or cur.get("price", 0) <= 0:
                update_signal_status(sig["id"], "FILTERED")
                continue

            current_price = cur["price"]
            prev_close = cur.get("prev_close", 0)

            # ATR 계산
            ohlcv = get_daily_ohlcv(code, days=20)
            if ohlcv:
                ohlcv.reverse()
                atr = calc_atr(ohlcv)
            else:
                atr = current_price * 0.02  # fallback: 2%

            # VWAP (일봉 근사)
            vwap = calc_vwap([{
                "high": cur.get("high", current_price),
                "low": cur.get("low", current_price),
                "close": current_price,
                "volume": cur.get("volume", 1),
            }])

            # 갭 시나리오 판단
            entry_result = evaluate_entry(
                signal={"code": code, "total_score": score},
                current_price=current_price,
                prev_close=prev_close,
                atr=atr,
                vwap=vwap,
            )

            action = entry_result["action"]

            if action == "BUY_MARKET":
                # 즉시 매수
                quantity = max(1, POSITION_SIZE // current_price)
                success = portfolio.buy(code, name, current_price, quantity, score, atr)
                if success:
                    update_signal_status(sig["id"], "BOUGHT")
                    logger.info("[ENTRY-A] %s %s 매수 %d주 @ %s원",
                                code, name, quantity, f"{current_price:,}")
                else:
                    update_signal_status(sig["id"], "FILTERED")

            elif action == "BUY_VWAP_PULLBACK":
                # VWAP 눌림: 현재가가 VWAP 이하면 매수
                target = entry_result.get("target_price", current_price)
                if current_price <= target:
                    quantity = max(1, POSITION_SIZE // current_price)
                    success = portfolio.buy(code, name, current_price, quantity, score, atr)
                    if success:
                        update_signal_status(sig["id"], "BOUGHT")
                        logger.info("[ENTRY-B] %s %s VWAP 눌림 매수 %d주 @ %s원",
                                    code, name, quantity, f"{current_price:,}")
                else:
                    logger.info("[ENTRY-B] %s VWAP 대기 (현재 %s > 타겟 %s)",
                                code, f"{current_price:,}", f"{target:,}")

            elif action in ("SKIP", "INVALIDATE"):
                update_signal_status(sig["id"], "FILTERED")
                logger.info("[ENTRY] %s %s → %s: %s",
                            code, name, action, entry_result.get("reason", ""))

            # 매수 가능 여부 재확인
            if not portfolio.can_buy():
                logger.info("매수 한도 도달 → 나머지 시그널 스킵")
                break

        logger.info("── 갭 시나리오 진입 체크 완료 ──")

    except Exception:
        logger.exception("진입 체크 중 예외 발생")


def run_position_monitor():
    """장중 포지션 모니터링 (손절/익절/시간무효화)."""
    try:
        if not is_market_open():
            return

        from app.api.rest import get_current_price, get_minute_chart
        from app.screener.indicators import calc_vwap
        from app.strategy.exit import check_exit
        from app.strategy.portfolio import Portfolio
        from app.strategy.risk import RiskManager
        from app.storage.db import update_position

        portfolio = Portfolio.instance()
        risk = RiskManager.instance()
        positions = portfolio.get_positions()

        if not positions:
            return

        logger.info("── 포지션 모니터링 (%d종목) ──", len(positions))

        for pos in positions:
            code = pos["code"]
            name = pos.get("name", "")

            # 현재가 조회
            cur = get_current_price(code)
            if not cur or cur.get("price", 0) <= 0:
                logger.debug("현재가 조회 실패: %s", code)
                continue

            current_price = cur["price"]

            # highest_price 갱신
            if current_price > pos.get("highest_price", 0):
                pos["highest_price"] = current_price
                update_position(code, highest_price=current_price)

            # 분봉으로 VWAP 계산
            minute_data = get_minute_chart(code)
            vwap = calc_vwap(minute_data) if minute_data else 0.0

            # 매도 판단
            exit_result = check_exit(pos, current_price, vwap, minute_data)
            action = exit_result["action"]

            if action == "SELL":
                result = portfolio.sell(code, current_price, pos["quantity"],
                                        exit_result["reason"])
                if result:
                    pnl_pct = result.get("profit_pct", 0)
                    if pnl_pct < 0:
                        risk.record_loss(pnl_pct)
                    else:
                        risk.record_win(pnl_pct)
                    logger.info("[EXIT] %s %s 전량매도 | %+.2f%% | 사유: %s",
                                code, name, pnl_pct, exit_result["reason"])

            elif action == "PARTIAL_SELL":
                ratio = exit_result.get("sell_ratio", 0.5)
                result = portfolio.partial_sell(code, current_price, ratio)
                if result:
                    pnl_pct = result.get("profit_pct", 0)
                    if pnl_pct < 0:
                        risk.record_loss(pnl_pct)
                    else:
                        risk.record_win(pnl_pct)
                    logger.info("[EXIT] %s %s 부분매도(%.0f%%) | %+.2f%% | 사유: %s",
                                code, name, ratio * 100, pnl_pct, exit_result["reason"])

    except Exception:
        logger.exception("포지션 모니터링 중 예외 발생")


def run_eod_screening():
    """종가 기반 시그널 스크리닝 (15:20) — 다음 날 진입 후보."""
    try:
        logger.info("── EOD 스크리닝 시작 ──")
        # EOD 스크리닝은 run_signal_screening과 동일 로직
        # 유니버스를 다시 가져와서 종가 기반으로 스코어링
        from app.api.rest import get_volume_rank, get_daily_ohlcv, get_investor_data
        from app.screener.universe import filter_universe
        from app.screener.scorer import score_stock
        from app.storage.db import save_signal, save_score_history
        from app.notifier import notify_signal
        from app.config import get_param

        threshold = get_param("SCORE_THRESHOLD", 60)

        candidates = get_volume_rank()
        if not candidates:
            logger.info("EOD: 후보 종목 없음")
            return

        filtered = filter_universe(candidates, ohlcv_fetcher=get_daily_ohlcv)
        if not filtered:
            logger.info("EOD: 유니버스 필터 통과 종목 없음")
            return

        signals_found = 0
        today_str = datetime.now().strftime("%Y-%m-%d")

        for stock in filtered:
            code = stock["code"]
            name = stock.get("name", "")

            ohlcv = get_daily_ohlcv(code, days=60)
            if not ohlcv or len(ohlcv) < 20:
                continue
            ohlcv.reverse()

            inv_data = get_investor_data(code)
            investor_list = [inv_data] if inv_data else None

            result = score_stock(code, ohlcv, investor_data=investor_list)
            total_score = result["total_score"]
            indicators = result.get("indicators", {})

            save_score_history(
                today_str, code, name, total_score,
                result.get("volume_score", 0),
                result.get("price_score", 0),
                result.get("supply_bonus", 0),
                indicators.get("volume_ratio", 0),
                indicators.get("atr", 0),
                indicators.get("close_quality", 0),
            )

            if total_score >= threshold:
                save_signal(
                    code, name, total_score,
                    volume_score=result.get("volume_score", 0),
                    price_score=result.get("price_score", 0),
                    supply_score=result.get("supply_bonus", 0),
                    volume_ratio=indicators.get("volume_ratio", 0),
                    atr=indicators.get("atr", 0),
                    status="DETECTED",
                )
                signals_found += 1

                notify_signal(code, name, total_score, {
                    "volume_score": result.get("volume_score", 0),
                    "price_score": result.get("price_score", 0),
                    "supply_score": result.get("supply_bonus", 0),
                    "volume_ratio": indicators.get("volume_ratio", 0),
                })

        logger.info("── EOD 스크리닝 완료: %d건 감지 ──", signals_found)

    except Exception:
        logger.exception("EOD 스크리닝 중 예외 발생")


def save_daily_snapshot():
    """일일 성과 스냅샷 저장."""
    try:
        logger.info("── 일일 스냅샷 저장 시작 ──")
        from app.strategy.portfolio import Portfolio
        from app.storage.db import (
            save_daily_performance, get_today_signals, get_today_trades,
            get_latest_performance,
        )
        from app.config import INITIAL_CAPITAL

        portfolio = Portfolio.instance()
        asset = portfolio.calc_total_asset()
        today = datetime.now().strftime("%Y-%m-%d")

        signals = get_today_signals()
        trades = get_today_trades()

        # 전일 대비 일일 수익률
        prev = get_latest_performance()
        prev_total = prev.get("total_asset", INITIAL_CAPITAL) if prev else INITIAL_CAPITAL
        daily_return = ((asset["total"] - prev_total) / prev_total * 100) if prev_total else 0

        save_daily_performance(
            today, asset["total"], asset["cash"], asset["stock_value"],
            round(daily_return, 2), asset["profit_pct"],
            len(portfolio.get_positions()), len(signals), len(trades),
        )

        logger.info(
            "── 일일 스냅샷 저장 완료: 총자산 %s원 (수익 %+.2f%%) ──",
            f"{asset['total']:,}", asset["profit_pct"],
        )

    except Exception:
        logger.exception("일일 스냅샷 저장 중 예외 발생")


def notify_daily_report():
    """텔레그램 일일 리포트 발송."""
    try:
        logger.info("── 일일 리포트 발송 ──")
        from app.strategy.portfolio import Portfolio
        from app.storage.db import (
            get_today_signals, get_today_trades, get_latest_performance,
        )
        from app.config import INITIAL_CAPITAL
        from app.notifier import notify_daily_report as send_report

        portfolio = Portfolio.instance()
        asset = portfolio.calc_total_asset()

        prev = get_latest_performance()
        prev_total = prev.get("total_asset", INITIAL_CAPITAL) if prev else INITIAL_CAPITAL
        daily_return = ((asset["total"] - prev_total) / prev_total * 100) if prev_total else 0

        signals = get_today_signals()
        trades = get_today_trades()

        # 보유 종목 손익 정보
        positions = portfolio.get_positions()
        pos_info = []
        for p in positions:
            buy = p.get("buy_price", 0)
            highest = p.get("highest_price", buy)
            pnl = ((highest - buy) / buy * 100) if buy else 0
            pos_info.append({"name": p.get("name", "?"), "pnl_pct": round(pnl, 1)})

        send_report({
            "total_asset": asset["total"],
            "cash": asset["cash"],
            "stock_value": asset["stock_value"],
            "daily_return_pct": round(daily_return, 2),
            "total_return_pct": asset["profit_pct"],
            "position_count": len(positions),
            "signals_count": len(signals),
            "trades_count": len(trades),
            "positions": pos_info,
        })

        logger.info("── 일일 리포트 발송 완료 ──")

    except Exception:
        logger.exception("일일 리포트 발송 중 예외 발생")


def reset_weekly_risk():
    """주간 리스크 상태 리셋 (매주 월요일 08:00)."""
    try:
        logger.info("── 주간 리스크 리셋 ──")
        from app.storage.db import reset_weekly_risk as db_reset_weekly
        db_reset_weekly()
        logger.info("── 주간 리스크 리셋 완료 ──")
    except Exception:
        logger.exception("주간 리스크 리셋 중 예외 발생")


# ── Flask 웹 서버 ──

def start_flask():
    """Flask 대시보드를 daemon 스레드로 실행."""
    from app.web.app import create_app
    app = create_app()
    logger.info("Flask 대시보드 시작 (port=%d)", FLASK_PORT)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)


# ── 메인 ──

def main():
    logger.info("=== VTP 스크리너 시작 ===")

    # DB 초기화 + 마이그레이션
    init_db()
    migrate_db()

    # 동적 설정 로드
    load_dynamic_config()

    # Flask 웹 대시보드 (daemon 스레드)
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 스케줄러 설정
    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 08:50: 유니버스 필터링 + 시그널 스크리닝 (전일 종가 기반)
    scheduler.add_job(
        run_universe_filter,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone="Asia/Seoul"),
        id="universe_filter",
        name="유니버스 필터링",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_signal_screening,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=52, timezone="Asia/Seoul"),
        id="signal_screening",
        name="시그널 스크리닝 (전일 종가)",
        misfire_grace_time=300,
    )

    # 09:00: 갭 시나리오 진입 체크
    scheduler.add_job(
        run_entry_check,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone="Asia/Seoul"),
        id="entry_check",
        name="갭 시나리오 진입 체크",
        misfire_grace_time=300,
    )

    # 09:00~15:20, 1분 간격: 포지션 모니터링
    scheduler.add_job(
        run_position_monitor,
        CronTrigger(
            day_of_week="mon-fri", hour="9-15", minute="*", timezone="Asia/Seoul",
        ),
        id="position_monitor",
        name="장중 포지션 모니터링 (1분)",
        misfire_grace_time=60,
    )

    # 15:20: 종가 기반 스크리닝
    scheduler.add_job(
        run_eod_screening,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=20, timezone="Asia/Seoul"),
        id="eod_screening",
        name="EOD 스크리닝",
        misfire_grace_time=300,
    )

    # 15:35: 일일 스냅샷 + 리포트
    scheduler.add_job(
        save_daily_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=35, timezone="Asia/Seoul"),
        id="daily_snapshot",
        name="일일 스냅샷 저장",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        notify_daily_report,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=36, timezone="Asia/Seoul"),
        id="daily_report",
        name="일일 텔레그램 리포트",
        misfire_grace_time=600,
    )

    # 매주 월요일 08:00: 주간 리스크 리셋
    scheduler.add_job(
        reset_weekly_risk,
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="Asia/Seoul"),
        id="weekly_risk_reset",
        name="주간 리스크 리셋",
        misfire_grace_time=600,
    )

    # 등록된 작업 로깅
    for job in scheduler.get_jobs():
        logger.info("등록된 작업: %s / 트리거: %s", job.name, job.trigger)

    # 시작 시 놓친 스크리닝 보충 실행 (평일, 08:52~15:19 사이 시작 시)
    now = datetime.now()
    if now.weekday() < 5 and dt_time(8, 52) < now.time() < dt_time(15, 20):
        logger.info("── 놓친 스크리닝 보충 실행 시작 ──")

        def _catchup():
            import time as _time
            _time.sleep(3)  # Flask 초기화 대기
            run_universe_filter()
            run_signal_screening()
            if dt_time(9, 0) <= datetime.now().time() <= dt_time(15, 20):
                run_entry_check()
            logger.info("── 놓친 스크리닝 보충 실행 완료 ──")

        catchup_thread = threading.Thread(target=_catchup, daemon=True)
        catchup_thread.start()

    # 종료 시그널 처리
    def shutdown(signum, frame):
        logger.info("종료 시그널 수신 (signal=%s), 스케줄러 중지...", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("스케줄러 시작 (평일 08:50~15:36 운영)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
