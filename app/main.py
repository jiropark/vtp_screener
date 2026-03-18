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
from app.web.app import create_app

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


# ── 장 운영 시간 체크 ──

def is_market_open() -> bool:
    """한국 주식시장 운영 시간 확인 (평일 09:00~15:30)."""
    now = datetime.now()
    # 주말 제외
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 0) <= t <= dt_time(15, 30)


# ── 스케줄 작업 래퍼 ──

def run_universe_filter():
    """유니버스 필터링 (시가총액, 거래대금 기준)."""
    try:
        logger.info("── 유니버스 필터링 시작 ──")
        # TODO: app.screener.universe 모듈 구현 후 연결
        # from app.screener.universe import filter_universe
        # filter_universe()
        logger.info("── 유니버스 필터링 완료 ──")
    except Exception:
        logger.exception("유니버스 필터링 중 예외 발생")


def run_signal_screening():
    """시그널 스크리닝 (전일 종가 기반 스코어링)."""
    try:
        logger.info("── 시그널 스크리닝 시작 ──")
        # TODO: app.screener.scorer 모듈 구현 후 연결
        # from app.screener.scorer import screen_signals
        # screen_signals()
        logger.info("── 시그널 스크리닝 완료 ──")
    except Exception:
        logger.exception("시그널 스크리닝 중 예외 발생")


def run_entry_check():
    """갭 시나리오 판단 + 매수 진입."""
    try:
        if not is_market_open():
            logger.info("장 미개장 → 진입 체크 스킵")
            return
        logger.info("── 갭 시나리오 진입 체크 시작 ──")
        # TODO: app.strategy.entry 모듈 구현 후 연결
        # from app.strategy.entry import check_entry
        # check_entry()
        logger.info("── 갭 시나리오 진입 체크 완료 ──")
    except Exception:
        logger.exception("진입 체크 중 예외 발생")


def run_position_monitor():
    """장중 포지션 모니터링 (손절/익절/시간무효화)."""
    try:
        if not is_market_open():
            return
        logger.info("── 포지션 모니터링 ──")
        # TODO: app.strategy.monitor 모듈 구현 후 연결
        # from app.strategy.monitor import monitor_positions
        # monitor_positions()
    except Exception:
        logger.exception("포지션 모니터링 중 예외 발생")


def run_eod_screening():
    """종가 기반 시그널 스크리닝 (15:20)."""
    try:
        logger.info("── EOD 스크리닝 시작 ──")
        # TODO: app.screener.scorer 모듈 구현 후 연결
        # from app.screener.scorer import screen_eod_signals
        # screen_eod_signals()
        logger.info("── EOD 스크리닝 완료 ──")
    except Exception:
        logger.exception("EOD 스크리닝 중 예외 발생")


def save_daily_snapshot():
    """일일 성과 스냅샷 저장."""
    try:
        logger.info("── 일일 스냅샷 저장 시작 ──")
        # TODO: app.storage.snapshot 모듈 구현 후 연결
        # from app.storage.snapshot import save_snapshot
        # save_snapshot()
        logger.info("── 일일 스냅샷 저장 완료 ──")
    except Exception:
        logger.exception("일일 스냅샷 저장 중 예외 발생")


def notify_daily_report():
    """텔레그램 일일 리포트 발송."""
    try:
        logger.info("── 일일 리포트 발송 ──")
        # TODO: app.notifier 모듈 구현 후 연결
        # from app.notifier import send_daily_report
        # send_daily_report()
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
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone="Asia/Seoul"),
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
        CronTrigger(day_of_week="mon-fri", hour=15, minute=35, timezone="Asia/Seoul"),
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

    # 종료 시그널 처리
    def shutdown(signum, frame):
        logger.info("종료 시그널 수신 (signal=%s), 스케줄러 중지...", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("스케줄러 시작 (평일 08:50~15:35 운영)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
