"""VTP 스크리너 웹 라우트.

대시보드, 시그널, 매매내역, 설정, JSON API 제공.
기존 app.storage.db 모듈의 CRUD 함수를 활용한다.
"""

import logging
from datetime import datetime, date
from flask import Blueprint, render_template, jsonify, request

from app.config import (
    INITIAL_CAPITAL, get_param, load_dynamic_config,
)

logger = logging.getLogger(__name__)

bp = Blueprint("dashboard", __name__)


# ── 헬퍼 함수 ──────────────────────────────────────────────


def _get_portfolio_summary() -> dict:
    """포트폴리오 요약 정보 조회."""
    from app.storage.db import get_positions, get_cash_balance

    initial = get_param("INITIAL_CAPITAL", INITIAL_CAPITAL)
    positions = get_positions()
    cash = get_cash_balance(initial)

    # 보유 주식 평가액 (현재가 미반영 → 매수가 기준 근사)
    stock_value = sum(p["buy_price"] * p["quantity"] for p in positions)
    total_asset = cash + stock_value
    return_pct = ((total_asset - initial) / initial * 100) if initial else 0

    return {
        "total_asset": total_asset,
        "cash": cash,
        "stock_value": stock_value,
        "positions": positions,
        "position_count": len(positions),
        "initial_capital": initial,
        "return_pct": round(return_pct, 2),
    }


def _get_daily_pnl() -> int:
    """오늘 실현 손익 합계."""
    from app.storage.db import get_today_trades
    trades = get_today_trades()
    return sum(t.get("pnl", 0) or 0 for t in trades if t.get("side") == "SELL")


def _get_risk_state() -> dict:
    """리스크 상태 조회."""
    from app.storage.db import get_risk_state
    return get_risk_state()


def _get_trade_stats() -> dict:
    """매매 통계 (승률, 평균수익, 평균손실, 손익비)."""
    from app.storage.db import get_trades
    all_trades = get_trades(limit=500)
    sells = [t for t in all_trades if t.get("side") == "SELL"]

    if not sells:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_win": 0, "avg_loss": 0, "profit_factor": 0}

    wins = [s for s in sells if (s.get("pnl") or 0) > 0]
    losses = [s for s in sells if (s.get("pnl") or 0) < 0]
    total = len(sells)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round(win_count / total * 100, 1) if total else 0
    avg_win = round(sum(w["pnl"] for w in wins) / win_count) if wins else 0
    avg_loss = round(sum(l["pnl"] for l in losses) / loss_count) if losses else 0
    total_win = sum(w["pnl"] for w in wins) if wins else 0
    total_loss = abs(sum(l["pnl"] for l in losses)) if losses else 0
    profit_factor = round(total_win / total_loss, 2) if total_loss else 0

    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }


# ── 페이지 라우트 ──────────────────────────────────────────


@bp.route("/")
def dashboard():
    """대시보드 (포트폴리오 현황 + 최근 시그널)."""
    try:
        summary = _get_portfolio_summary()
    except Exception:
        logger.exception("포트폴리오 요약 조회 실패")
        summary = {
            "total_asset": 0, "cash": 0, "stock_value": 0,
            "positions": [], "position_count": 0,
            "initial_capital": INITIAL_CAPITAL, "return_pct": 0,
        }

    try:
        daily_pnl = _get_daily_pnl()
    except Exception:
        daily_pnl = 0

    try:
        risk = _get_risk_state()
    except Exception:
        risk = {"daily_loss_pct": 0, "weekly_loss_pct": 0, "consecutive_losses": 0}

    # 최근 시그널 5개
    try:
        from app.storage.db import get_signals
        signals = get_signals(limit=5)
    except Exception:
        signals = []

    return render_template(
        "dashboard.html",
        summary=summary,
        daily_pnl=daily_pnl,
        risk=risk,
        signals=signals,
    )


@bp.route("/signals")
def signals_page():
    """시그널 히스토리 페이지."""
    date_filter = request.args.get("date", "")

    try:
        from app.storage.db import get_signals, _conn
        if date_filter:
            with _conn() as c:
                rows = c.execute(
                    "SELECT * FROM signals WHERE DATE(timestamp) = ? "
                    "ORDER BY id DESC LIMIT 200",
                    (date_filter,),
                ).fetchall()
                signals = [dict(r) for r in rows]
        else:
            signals = get_signals(limit=200)
    except Exception:
        logger.exception("시그널 조회 실패")
        signals = []

    return render_template("signals.html", signals=signals, date_filter=date_filter)


@bp.route("/trades")
def trades_page():
    """매매 내역 페이지."""
    try:
        from app.storage.db import get_trades
        trades = get_trades(limit=100)
    except Exception:
        logger.exception("매매 내역 조회 실패")
        trades = []

    stats = _get_trade_stats()
    return render_template("trades.html", trades=trades, stats=stats)


@bp.route("/settings")
def settings_page():
    """파라미터 튜닝 페이지."""
    try:
        from app.storage.db import get_all_dynamic_config
        dynamic_raw = get_all_dynamic_config()
        # updated_at 도 가져오기
        from app.storage.db import _conn
        with _conn() as c:
            rows = c.execute(
                "SELECT param_name, param_value, updated_at FROM dynamic_config "
                "ORDER BY param_name"
            ).fetchall()
            dynamic = {r["param_name"]: {"value": r["param_value"], "updated_at": r["updated_at"]}
                       for r in rows}
    except Exception:
        dynamic = {}

    # 튜닝 가능 파라미터 그룹
    param_groups = {
        "진입 조건": [
            ("SCORE_THRESHOLD", "매수 시그널 최소 점수", get_param("SCORE_THRESHOLD", 60)),
            ("MIN_MARKET_CAP", "최소 시가총액", get_param("MIN_MARKET_CAP", 100_000_000_000)),
            ("MIN_AVG_TRADE_AMOUNT", "최소 평균 거래대금", get_param("MIN_AVG_TRADE_AMOUNT", 1_000_000_000)),
        ],
        "목표가 순항": [
            ("TARGET_UPSIDE_MIN", "최소 상승여력(%)", get_param("TARGET_UPSIDE_MIN", 10.0)),
            ("TARGET_UPSIDE_MAX", "최대 상승여력(%)", get_param("TARGET_UPSIDE_MAX", 60.0)),
            ("TARGET_UPSIDE_SWEET_MIN", "이상적 구간 시작(%)", get_param("TARGET_UPSIDE_SWEET_MIN", 15.0)),
            ("TARGET_UPSIDE_SWEET_MAX", "이상적 구간 끝(%)", get_param("TARGET_UPSIDE_SWEET_MAX", 40.0)),
            ("CRUISE_R2_MIN", "최소 R² (추세 일관성)", get_param("CRUISE_R2_MIN", 0.6)),
            ("CRUISE_DRAWDOWN_MAX", "최대 고점 대비 낙폭(%)", get_param("CRUISE_DRAWDOWN_MAX", 5.0)),
            ("PULLBACK_MAX_DIST_PCT", "MA5 최대 괴리율(%)", get_param("PULLBACK_MAX_DIST_PCT", 2.0)),
        ],
        "손절 관리": [
            ("ATR_STOP_MULTIPLIER", "ATR 손절 배수", get_param("ATR_STOP_MULTIPLIER", 1.2)),
            ("ATR_TRAILING_MULTIPLIER", "트레일링 스탑 ATR 배수", get_param("ATR_TRAILING_MULTIPLIER", 1.2)),
            ("MAX_HOLD_DAYS", "최대 보유일수", get_param("MAX_HOLD_DAYS", 5)),
        ],
        "익절 관리": [
            ("ATR_TAKE_PROFIT_1", "1차 익절 ATR 배수", get_param("ATR_TAKE_PROFIT_1", 2.0)),
            ("ATR_TAKE_PROFIT_2", "2차 익절 ATR 배수", get_param("ATR_TAKE_PROFIT_2", 3.0)),
        ],
        "리스크 관리": [
            ("MAX_POSITIONS", "최대 동시 보유 종목수", get_param("MAX_POSITIONS", 5)),
            ("POSITION_SIZE_PCT", "포지션 크기(%)", get_param("POSITION_SIZE_PCT", 10)),
            ("DAILY_LOSS_LIMIT", "일일 최대 손실률(%)", get_param("DAILY_LOSS_LIMIT", -2.0)),
            ("WEEKLY_LOSS_LIMIT", "주간 최대 손실률(%)", get_param("WEEKLY_LOSS_LIMIT", -5.0)),
            ("CONSECUTIVE_LOSS_COOLDOWN", "연속 손실 쿨다운 횟수", get_param("CONSECUTIVE_LOSS_COOLDOWN", 3)),
        ],
    }

    return render_template(
        "settings.html",
        param_groups=param_groups,
        dynamic=dynamic,
    )


# ── JSON API ────────────────────────────────────────────────


@bp.route("/api/portfolio")
def api_portfolio():
    """포트폴리오 JSON."""
    try:
        return jsonify(_get_portfolio_summary())
    except Exception:
        logger.exception("API 포트폴리오 오류")
        return jsonify({"error": "조회 실패"}), 500


@bp.route("/api/signals")
def api_signals():
    """최근 50개 시그널 JSON."""
    try:
        from app.storage.db import get_signals
        return jsonify(get_signals(limit=50))
    except Exception:
        logger.exception("API 시그널 오류")
        return jsonify({"error": "조회 실패"}), 500


@bp.route("/api/trades")
def api_trades():
    """최근 50개 거래 JSON."""
    try:
        from app.storage.db import get_trades
        return jsonify(get_trades(limit=50))
    except Exception:
        logger.exception("API 거래 오류")
        return jsonify({"error": "조회 실패"}), 500


@bp.route("/api/performance")
def api_performance():
    """일일 성과 데이터 JSON (Chart.js용)."""
    try:
        from app.storage.db import get_daily_performances
        perfs = get_daily_performances(limit=90)
        # 시간순 정렬 (오래된→최신)
        perfs.reverse()
        return jsonify({
            "dates": [p["date"] for p in perfs],
            "total_assets": [p["total_asset"] for p in perfs],
            "daily_returns": [p["daily_return_pct"] for p in perfs],
            "cash": [p["cash"] for p in perfs],
        })
    except Exception:
        logger.exception("API 성과 오류")
        return jsonify({"error": "조회 실패"}), 500


@bp.route("/api/scores/<code>")
def api_scores(code):
    """종목별 스코어 브레이크다운 JSON."""
    try:
        from app.storage.db import get_score_history
        return jsonify(get_score_history(code=code, limit=30))
    except Exception:
        logger.exception("API 스코어 오류: %s", code)
        return jsonify({"error": "조회 실패"}), 500


@bp.route("/api/settings", methods=["POST"])
def api_settings():
    """동적 설정 파라미터 업데이트."""
    try:
        data = request.get_json(force=True)
        param_name = data.get("param_name", "").strip()
        param_value = str(data.get("param_value", "")).strip()

        if not param_name or not param_value:
            return jsonify({"error": "파라미터 이름/값 필요"}), 400

        from app.storage.db import set_dynamic_config
        set_dynamic_config(param_name, param_value)

        # 캐시 갱신
        load_dynamic_config()

        logger.info("설정 변경: %s = %s", param_name, param_value)
        return jsonify({"ok": True, "param_name": param_name, "param_value": param_value})

    except Exception:
        logger.exception("API 설정 변경 오류")
        return jsonify({"error": "설정 변경 실패"}), 500
