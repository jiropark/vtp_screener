"""SQLite DDL — VTP 스크리너 테이블 정의.

signals:            스코어링 시그널 기록
trades:             매수/매도 거래 내역
positions:          현재 보유 포지션
daily_performance:  일일 성과 스냅샷
score_history:      종목별 스코어 이력 (분석용)
risk_state:         리스크 관리 상태 (단일 행)
dynamic_config:     런타임 파라미터 오버라이드
"""

TABLES_DDL = [
    # ── 시그널 ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        score REAL DEFAULT 0,
        volume_score REAL DEFAULT 0,
        price_score REAL DEFAULT 0,
        supply_score REAL DEFAULT 0,
        volume_ratio REAL DEFAULT 0,
        close_vs_high REAL DEFAULT 0,
        atr REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'DETECTED'
            CHECK(status IN ('DETECTED','BOUGHT','EXPIRED','FILTERED'))
    )
    """,

    # ── 거래 내역 ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
        price INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        fee INTEGER DEFAULT 0,
        tax INTEGER DEFAULT 0,
        reason TEXT,
        score REAL DEFAULT 0,
        pnl INTEGER DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── 포지션 ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS positions (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        buy_price INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        original_quantity INTEGER NOT NULL,
        highest_price INTEGER DEFAULT 0,
        atr_at_entry REAL DEFAULT 0,
        entry_score REAL DEFAULT 0,
        entry_date TEXT NOT NULL,
        partial_sold INTEGER DEFAULT 0
    )
    """,

    # ── 일일 성과 ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS daily_performance (
        date TEXT PRIMARY KEY,
        total_asset INTEGER DEFAULT 0,
        cash INTEGER DEFAULT 0,
        stock_value INTEGER DEFAULT 0,
        daily_return_pct REAL DEFAULT 0,
        total_return_pct REAL DEFAULT 0,
        position_count INTEGER DEFAULT 0,
        signals_count INTEGER DEFAULT 0,
        trades_count INTEGER DEFAULT 0
    )
    """,

    # ── 스코어 이력 ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS score_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        total_score REAL DEFAULT 0,
        volume_score REAL DEFAULT 0,
        price_score REAL DEFAULT 0,
        supply_bonus REAL DEFAULT 0,
        volume_ratio REAL DEFAULT 0,
        atr REAL DEFAULT 0,
        close_quality REAL DEFAULT 0
    )
    """,

    # ── 리스크 상태 (항상 id=1인 단일 행) ─────────────────
    """
    CREATE TABLE IF NOT EXISTS risk_state (
        id INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
        daily_loss_pct REAL DEFAULT 0,
        weekly_loss_pct REAL DEFAULT 0,
        consecutive_losses INTEGER DEFAULT 0,
        last_loss_date TEXT,
        cooldown_until TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── 동적 설정 ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS dynamic_config (
        param_name TEXT PRIMARY KEY,
        param_value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]
