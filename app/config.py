"""VTP 스크리너 설정 모듈.

.env 파일에서 KIS API 키, 텔레그램 봇 토큰 등을 로드한다.
전략 파라미터는 기본값을 제공하되, DB dynamic_config로 런타임 오버라이드 가능.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# .env 로드 (프로젝트 루트 기준)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ── KIS API ──────────────────────────────────────────────
APP_KEY = os.getenv("KIS_APP_KEY", "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
ACCOUNT_SUFFIX = "01"
IS_REAL = os.getenv("KIS_IS_REAL", "false").lower() == "true"

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_VTS = "https://openapivts.koreainvestment.com:29443"
BASE_URL = BASE_URL_REAL if IS_REAL else BASE_URL_VTS

# ── 텔레그램 ─────────────────────────────────────────────
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# ── 자금 관리 ─────────────────────────────────────────────
INITIAL_CAPITAL = 5_000_000          # 초기 자본금
MAX_POSITIONS = 5                     # 최대 동시 보유 종목 수
POSITION_SIZE_PCT = 10                # 총자산 대비 포지션 크기 (%)

# ── ATR 기반 손익 관리 ────────────────────────────────────
ATR_PERIOD = 14                       # ATR 계산 기간
ATR_STOP_MULTIPLIER = 1.2            # 손절: 진입가 - ATR × 배수
ATR_TAKE_PROFIT_1 = 2.0              # 1차 익절: 진입가 + ATR × 배수
ATR_TAKE_PROFIT_2 = 3.0              # 2차 익절: 진입가 + ATR × 배수
ATR_TRAILING_MULTIPLIER = 1.2        # 트레일링 스탑: 최고가 - ATR × 배수

# ── 리스크 관리 ───────────────────────────────────────────
MAX_HOLD_DAYS = 5                     # 최대 보유일수 (순항 전략)
DAILY_LOSS_LIMIT = -2.0              # 일일 최대 손실률 (%)
WEEKLY_LOSS_LIMIT = -5.0             # 주간 최대 손실률 (%)
CONSECUTIVE_LOSS_COOLDOWN = 3         # 연속 손실 N회 시 쿨다운 진입

# ── 스코어링 ──────────────────────────────────────────────
SCORE_THRESHOLD = 60                  # 매수 시그널 최소 점수 (백테스트로 튜닝)

# ── 목표가 순항 전략 ──────────────────────────────────────
# 목표가 괴리율 (현재가 대비 목표가 상승여력)
TARGET_UPSIDE_MIN = 10.0              # 최소 상승여력 (%)
TARGET_UPSIDE_MAX = 60.0              # 최대 상승여력 (%) - 너무 높으면 비현실적
TARGET_UPSIDE_SWEET_MIN = 15.0        # 이상적 구간 시작 (%)
TARGET_UPSIDE_SWEET_MAX = 40.0        # 이상적 구간 끝 (%)

# 순항도 지표
CRUISE_R2_MIN = 0.6                   # 선형회귀 R² 최소값 (추세 일관성)
CRUISE_MA_SHORT = 5                   # 단기 이평
CRUISE_MA_MID = 20                    # 중기 이평
CRUISE_MA_LONG = 60                   # 장기 이평
CRUISE_DRAWDOWN_MAX = 5.0             # 최근 고점 대비 최대 낙폭 (%)

# 진입 (눌림목)
PULLBACK_MA_TOUCH = 5                 # 단기 이평 터치 시 진입
PULLBACK_MAX_DIST_PCT = 2.0           # 이평선 대비 최대 괴리 (%)

# 수급 (10일 기준)
SUPPLY_LOOKBACK_DAYS = 10             # 수급 판단 기간
SUPPLY_NET_BUY_RATIO = 0.5            # 순매수 일수 비율 (50% 이상이면 우호적)

# ── 필터링 기준 ───────────────────────────────────────────
MIN_MARKET_CAP = 100_000_000_000     # 최소 시가총액 (1000억)
MIN_AVG_TRADE_AMOUNT = 1_000_000_000  # 최소 평균 거래대금 (10억)

# ── 웹 서버 ───────────────────────────────────────────────
FLASK_PORT = int(os.getenv("FLASK_PORT", "8092"))

# ── 수수료/세금/슬리피지 ──────────────────────────────────
FEE_RATE = 0.00015                    # 매수/매도 수수료 0.015%
BUY_FEE_RATE = FEE_RATE               # 매수 수수료
SELL_FEE_RATE = FEE_RATE              # 매도 수수료
TAX_RATE = 0.0018                     # 거래세 0.18%
SELL_TAX_RATE = TAX_RATE              # 매도 세금 (별칭)
SLIPPAGE = 0.001                      # 슬리피지 0.1%

# ── 포지션 / 쿨다운 ──────────────────────────────────────────
POSITION_SIZE = 500_000               # 종목당 최대 매수 금액 (50만원 = 자금 500만 × 10%)
MIN_CASH_RATIO = 0.3                  # 최소 현금 비율 30%
COOLDOWN_MINUTES = 30                 # 매도 후 재매수 쿨다운 (분)

# ── 스케줄 ────────────────────────────────────────────────
MARKET_OPEN = "09:00"
MARKET_CLOSE = "15:30"


# ── 동적 설정 ─────────────────────────────────────────────

# 런타임 오버라이드 캐시 (DB에서 로드)
_dynamic_overrides: dict[str, str] = {}


def load_dynamic_config():
    """DB의 dynamic_config 테이블에서 파라미터 오버라이드를 로드한다.

    앱 시작 시, 또는 설정 변경 시 호출.
    순환 임포트 방지를 위해 함수 내에서 db 모듈을 임포트한다.
    """
    global _dynamic_overrides
    try:
        from app.storage.db import get_all_dynamic_config
        _dynamic_overrides = get_all_dynamic_config()
        logger.info("동적 설정 %d개 로드", len(_dynamic_overrides))
    except Exception:
        logger.warning("동적 설정 로드 실패 (DB 미초기화 가능)")


def get_param(name: str, default=None):
    """설정 파라미터를 가져온다.

    우선순위: dynamic_config DB → 모듈 전역변수 → default.
    DB 값은 문자열이므로, 모듈 변수의 타입에 맞게 변환한다.
    """
    # 1) 동적 오버라이드
    if name in _dynamic_overrides:
        raw = _dynamic_overrides[name]
        # 모듈 전역변수 타입에 맞춰 변환
        module_val = globals().get(name)
        if module_val is not None:
            try:
                if isinstance(module_val, bool):
                    return raw.lower() in ("true", "1", "yes")
                elif isinstance(module_val, int):
                    return int(float(raw))
                elif isinstance(module_val, float):
                    return float(raw)
            except (ValueError, TypeError):
                pass
        return raw

    # 2) 모듈 전역변수
    if name in globals():
        return globals()[name]

    return default
