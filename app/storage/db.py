"""SQLite 데이터 접근 계층 (VTP 스크리너).

WAL 모드, 컨텍스트 매니저 패턴.
각 테이블별 CRUD 함수를 제공한다.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from typing import Any

from app.models import TABLES_DDL

logger = logging.getLogger(__name__)

DB_PATH = "/data/vtp.db"


@contextmanager
def _conn():
    """WAL 모드 SQLite 커넥션 컨텍스트."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ── 초기화 ──────────────────────────────────────────────

def init_db():
    """모든 테이블 생성."""
    with _conn() as c:
        for ddl in TABLES_DDL:
            c.execute(ddl)
    # risk_state 초기 행 삽입 (없으면)
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO risk_state (id) VALUES (1)"
        )
    logger.info("DB 초기화 완료: %s", DB_PATH)


def migrate_db():
    """스키마 마이그레이션. 컬럼 추가 등을 여기에 누적한다."""
    migrations = [
        # 예시: ("ALTER TABLE positions ADD COLUMN stop_price INTEGER DEFAULT 0",),
    ]
    with _conn() as c:
        for sql_tuple in migrations:
            try:
                c.execute(sql_tuple[0])
                logger.info("마이그레이션 적용: %s", sql_tuple[0][:60])
            except sqlite3.OperationalError:
                pass  # 이미 적용된 마이그레이션


# ══════════════════════════════════════════════════════════
# signals
# ══════════════════════════════════════════════════════════

def save_signal(code: str, name: str, score: float,
                volume_score: float = 0, price_score: float = 0,
                supply_score: float = 0, volume_ratio: float = 0,
                close_vs_high: float = 0, atr: float = 0,
                status: str = "DETECTED") -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO signals "
            "(code, name, score, volume_score, price_score, supply_score, "
            "volume_ratio, close_vs_high, atr, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, name, score, volume_score, price_score, supply_score,
             volume_ratio, close_vs_high, atr, status),
        )
        return cur.lastrowid


def update_signal_status(signal_id: int, status: str):
    with _conn() as c:
        c.execute(
            "UPDATE signals SET status = ? WHERE id = ?",
            (status, signal_id),
        )


def get_signals(limit: int = 50, status: str | None = None) -> list[dict]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM signals WHERE status = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_today_signals() -> list[dict]:
    today = date.today().isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM signals WHERE DATE(timestamp) = ? "
            "ORDER BY score DESC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# trades
# ══════════════════════════════════════════════════════════

def save_trade(code: str, name: str, side: str, price: int,
               quantity: int, amount: int, fee: int = 0, tax: int = 0,
               reason: str = "", score: float = 0,
               pnl: int = 0, pnl_pct: float = 0) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO trades "
            "(code, name, side, price, quantity, amount, fee, tax, "
            "reason, score, pnl, pnl_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, name, side, price, quantity, amount, fee, tax,
             reason, score, pnl, pnl_pct),
        )
        return cur.lastrowid


def get_trades(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_today_trades() -> list[dict]:
    today = date.today().isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades WHERE DATE(created_at) = ? "
            "ORDER BY created_at DESC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_trades_since(since_date: str) -> list[dict]:
    """특정 날짜 이후의 거래 내역 (주간 손익 계산용)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades WHERE DATE(created_at) >= ? "
            "ORDER BY created_at",
            (since_date,),
        ).fetchall()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# positions
# ══════════════════════════════════════════════════════════

def save_position(code: str, name: str, buy_price: int, quantity: int,
                  atr_at_entry: float = 0, entry_score: float = 0) -> None:
    today = date.today().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO positions "
            "(code, name, buy_price, quantity, original_quantity, "
            "highest_price, atr_at_entry, entry_score, entry_date, partial_sold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (code, name, buy_price, quantity, quantity,
             buy_price, atr_at_entry, entry_score, today),
        )


def get_positions() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM positions ORDER BY entry_date"
        ).fetchall()
        return [dict(r) for r in rows]


def get_position(code: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM positions WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None


def update_position(code: str, **kwargs) -> None:
    """가변 컬럼 업데이트. ex) update_position("005930", highest_price=80000)"""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [code]
    with _conn() as c:
        c.execute(f"UPDATE positions SET {cols} WHERE code = ?", vals)


def delete_position(code: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM positions WHERE code = ?", (code,))


def count_positions() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]


# ══════════════════════════════════════════════════════════
# daily_performance
# ══════════════════════════════════════════════════════════

def save_daily_performance(dt: str, total_asset: int, cash: int,
                           stock_value: int, daily_return_pct: float,
                           total_return_pct: float, position_count: int,
                           signals_count: int, trades_count: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO daily_performance "
            "(date, total_asset, cash, stock_value, daily_return_pct, "
            "total_return_pct, position_count, signals_count, trades_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dt, total_asset, cash, stock_value, daily_return_pct,
             total_return_pct, position_count, signals_count, trades_count),
        )


def get_daily_performances(limit: int = 30) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM daily_performance ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_performance() -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM daily_performance ORDER BY date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════
# score_history
# ══════════════════════════════════════════════════════════

def save_score_history(dt: str, code: str, name: str,
                       total_score: float, volume_score: float = 0,
                       price_score: float = 0, supply_bonus: float = 0,
                       volume_ratio: float = 0, atr: float = 0,
                       close_quality: float = 0) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO score_history "
            "(date, code, name, total_score, volume_score, price_score, "
            "supply_bonus, volume_ratio, atr, close_quality) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dt, code, name, total_score, volume_score, price_score,
             supply_bonus, volume_ratio, atr, close_quality),
        )
        return cur.lastrowid


def get_score_history(code: str = None, limit: int = 100) -> list[dict]:
    with _conn() as c:
        if code:
            rows = c.execute(
                "SELECT * FROM score_history WHERE code = ? "
                "ORDER BY date DESC LIMIT ?",
                (code, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM score_history ORDER BY date DESC, "
                "total_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# risk_state
# ══════════════════════════════════════════════════════════

def get_risk_state() -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM risk_state WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}


def update_risk_state(**kwargs) -> None:
    """리스크 상태 업데이트. 항상 id=1 행을 갱신한다."""
    if not kwargs:
        return
    kwargs["updated_at"] = datetime.now().isoformat()
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values())
    with _conn() as c:
        c.execute(f"UPDATE risk_state SET {cols} WHERE id = 1", vals)


def reset_daily_risk():
    """일일 리스크 카운터 초기화 (장 시작 시)."""
    update_risk_state(daily_loss_pct=0)


def reset_weekly_risk():
    """주간 리스크 카운터 초기화 (월요일 장 시작 시)."""
    update_risk_state(weekly_loss_pct=0)


# ══════════════════════════════════════════════════════════
# dynamic_config
# ══════════════════════════════════════════════════════════

def set_dynamic_config(param_name: str, param_value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO dynamic_config "
            "(param_name, param_value, updated_at) VALUES (?, ?, ?)",
            (param_name, param_value, datetime.now().isoformat()),
        )


def get_dynamic_config(param_name: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT param_value FROM dynamic_config WHERE param_name = ?",
            (param_name,),
        ).fetchone()
        return row[0] if row else None


def get_all_dynamic_config() -> dict[str, str]:
    with _conn() as c:
        rows = c.execute(
            "SELECT param_name, param_value FROM dynamic_config"
        ).fetchall()
        return {r[0]: r[1] for r in rows}


def delete_dynamic_config(param_name: str) -> None:
    with _conn() as c:
        c.execute(
            "DELETE FROM dynamic_config WHERE param_name = ?",
            (param_name,),
        )


# ══════════════════════════════════════════════════════════
# 현금 계산 (trades 기반)
# ══════════════════════════════════════════════════════════

def get_cash_balance(initial_capital: int) -> int:
    """trades 테이블 기반 현금 잔고 계산."""
    with _conn() as c:
        buy_total = c.execute(
            "SELECT COALESCE(SUM(amount + fee), 0) FROM trades WHERE side = 'BUY'"
        ).fetchone()[0]
        sell_total = c.execute(
            "SELECT COALESCE(SUM(amount - fee - tax), 0) FROM trades WHERE side = 'SELL'"
        ).fetchone()[0]
        return initial_capital - buy_total + sell_total
