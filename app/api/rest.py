"""KIS Open API REST 호출 모듈 (VTP 스크리너).

Rate limit: 초당 20건 (0.05s sleep).
모든 함수는 에러 시 빈 dict/list를 반환하여 서비스 중단을 방지한다.
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from app.auth import get_access_token
from app.config import APP_KEY, APP_SECRET, BASE_URL, IS_REAL

logger = logging.getLogger(__name__)

_session = requests.Session()
_last_request_time: float = 0.0
_RATE_LIMIT_INTERVAL = 0.05  # 초당 20건


# ══════════════════════════════════════════════════════════
# 공통 요청
# ══════════════════════════════════════════════════════════

def _request(
    method: str,
    path: str,
    headers_extra: dict | None = None,
    params: dict | None = None,
    body: dict | None = None,
) -> dict:
    """공통 API 호출. Rate limit 준수, 에러 핸들링."""
    global _last_request_time

    # rate limit
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_INTERVAL:
        time.sleep(_RATE_LIMIT_INTERVAL - elapsed)

    url = f"{BASE_URL}{path}"
    token = get_access_token()

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "custtype": "P",
    }
    if headers_extra:
        headers.update(headers_extra)

    try:
        _last_request_time = time.time()
        resp = _session.request(
            method, url,
            headers=headers,
            params=params,
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # KIS API 에러 코드 체크
        rt_cd = data.get("rt_cd")
        if rt_cd and rt_cd != "0":
            msg = data.get("msg1", "unknown error")
            logger.error("KIS API error [%s] %s: %s", path, rt_cd, msg)
            return {}

        return data
    except requests.RequestException as exc:
        logger.error("HTTP error [%s]: %s", path, exc)
        return {}
    except Exception as exc:
        logger.error("Unexpected error [%s]: %s", path, exc)
        return {}


# ══════════════════════════════════════════════════════════
# 현재가 조회
# ══════════════════════════════════════════════════════════

def get_current_price(code: str) -> dict:
    """현재가 시세 조회.

    Returns:
        {code, name, price, change_rate, volume, trade_amount, market_cap,
         high, low, open, prev_close, volume_ratio}
        에러 시 빈 dict.
    """
    data = _request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        headers_extra={"tr_id": "FHKST01010100"},
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        },
    )
    if not data:
        return {}

    out = data.get("output", {})
    if not out:
        return {}

    try:
        return {
            "code": code,
            "name": out.get("hts_kor_isnm", ""),
            "price": int(out.get("stck_prpr", 0)),
            "change_rate": float(out.get("prdy_ctrt", 0)),
            "volume": int(out.get("acml_vol", 0)),
            "trade_amount": int(out.get("acml_tr_pbmn", 0)),
            "market_cap": int(out.get("hts_avls", 0)) * 100_000_000,
            "high": int(out.get("stck_hgpr", 0)),
            "low": int(out.get("stck_lwpr", 0)),
            "open": int(out.get("stck_oprc", 0)),
            "prev_close": int(out.get("stck_sdpr", 0)),
            "volume_ratio": float(out.get("vol_tnrt", 0)),
        }
    except (ValueError, TypeError) as exc:
        logger.warning("현재가 파싱 실패 [%s]: %s", code, exc)
        return {}


# ══════════════════════════════════════════════════════════
# 일봉 데이터 (OHLCV)
# ══════════════════════════════════════════════════════════

def get_daily_ohlcv(code: str, days: int = 60) -> list[dict]:
    """일봉 차트 데이터 조회.

    KIS API는 한 번에 최대 100건을 반환한다.
    날짜 역순(최신→과거)으로 반환.

    Returns:
        [{date, open, high, low, close, volume, trade_amount}, ...]
        에러 시 빈 list.
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

    data = _request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers_extra={
            "tr_id": "FHKST03010100",
        },
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",  # 수정주가
        },
    )
    if not data:
        return []

    output2 = data.get("output2", [])
    if not output2:
        return []

    result = []
    for item in output2[:days]:
        try:
            result.append({
                "date": item.get("stck_bsop_date", ""),
                "open": int(item.get("stck_oprc", 0)),
                "high": int(item.get("stck_hgpr", 0)),
                "low": int(item.get("stck_lwpr", 0)),
                "close": int(item.get("stck_clpr", 0)),
                "volume": int(item.get("acml_vol", 0)),
                "trade_amount": int(item.get("acml_tr_pbmn", 0)),
            })
        except (ValueError, TypeError):
            continue

    return result


# ══════════════════════════════════════════════════════════
# 분봉 데이터
# ══════════════════════════════════════════════════════════

def get_minute_chart(code: str, time_unit: str = "1") -> list[dict]:
    """분봉 차트 데이터 조회.

    Returns:
        [{time, open, high, low, close, volume, trade_amount}, ...]
        최신 데이터가 리스트 앞. 에러 시 빈 list.
    """
    now = datetime.now().strftime("%H%M%S")

    data = _request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers_extra={"tr_id": "FHKST03010200"},
        params={
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": now,
            "FID_PW_DATA_INCU_YN": "Y",
        },
    )
    if not data:
        return []

    output2 = data.get("output2", [])
    if not output2:
        return []

    result = []
    for item in output2:
        try:
            result.append({
                "time": item.get("stck_cntg_hour", ""),
                "open": int(item.get("stck_oprc", 0)),
                "high": int(item.get("stck_hgpr", 0)),
                "low": int(item.get("stck_lwpr", 0)),
                "close": int(item.get("stck_prpr", 0)),
                "volume": int(item.get("cntg_vol", 0)),
                "trade_amount": int(item.get("acml_tr_pbmn", 0)),
            })
        except (ValueError, TypeError):
            continue

    return result


# ══════════════════════════════════════════════════════════
# 투자자별 매매동향
# ══════════════════════════════════════════════════════════

def get_investor_data(code: str) -> dict:
    """투자자별 매매동향 (외국인/기관 수급).

    Returns:
        {foreign_net, inst_net, individual_net,
         foreign_buy, foreign_sell, inst_buy, inst_sell}
        에러 시 빈 dict.
    """
    today = datetime.now().strftime("%Y%m%d")

    data = _request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-investor",
        headers_extra={"tr_id": "FHKST01010900"},
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": today,
            "FID_INPUT_DATE_2": today,
        },
    )
    if not data:
        return {}

    output = data.get("output", [])
    if not output:
        return {}

    row = output[0] if isinstance(output, list) else output

    try:
        foreign_net = int(row.get("frgn_ntby_qty", 0) or 0)
        inst_net = int(row.get("orgn_ntby_qty", 0) or 0)
        individual_net = int(row.get("prsn_ntby_qty", 0) or 0)

        return {
            "foreign_net": foreign_net,
            "foreign_buy": max(foreign_net, 0),
            "foreign_sell": abs(min(foreign_net, 0)),
            "inst_net": inst_net,
            "inst_buy": max(inst_net, 0),
            "inst_sell": abs(min(inst_net, 0)),
            "individual_net": individual_net,
        }
    except (ValueError, TypeError) as exc:
        logger.warning("투자자 데이터 파싱 실패 [%s]: %s", code, exc)
        return {}


# ══════════════════════════════════════════════════════════
# 거래량 상위 종목
# ══════════════════════════════════════════════════════════

def get_volume_rank() -> list[dict]:
    """거래량 상위 종목 조회.

    모의투자 앱키로는 KIS 거래량 순위 API가 빈 결과를 반환하므로
    네이버 금융 모바일 API에서 KOSPI+KOSDAQ 거래량 상위를 가져온다.

    Returns:
        [{code, name, price, change_rate, volume, trade_amount, market_cap}, ...]
        에러 시 빈 list.
    """
    # 모의투자면 네이버 API 사용
    if not IS_REAL:
        return _get_volume_rank_naver()

    # 실전투자: KIS API 사용
    data = _request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/volume-rank",
        headers_extra={"tr_id": "FHPST01710000"},
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "0",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": "",
        },
    )
    if not data:
        return _get_volume_rank_naver()  # KIS 실패 시 네이버 폴백

    output = data.get("output", [])
    result = []
    for item in output:
        try:
            code = item.get("mksc_shrn_iscd", "")
            if not code or len(code) != 6:
                continue
            result.append({
                "code": code,
                "name": item.get("hts_kor_isnm", ""),
                "price": int(item.get("stck_prpr", 0)),
                "change_rate": float(item.get("prdy_ctrt", 0)),
                "volume": int(item.get("acml_vol", 0)),
                "trade_amount": int(item.get("acml_tr_pbmn", 0)),
                "market_cap": int(item.get("hts_avls", 0)) * 100_000_000,
            })
        except (ValueError, TypeError):
            continue

    if not result:
        return _get_volume_rank_naver()

    logger.info("KIS 거래량 순위: %d종목", len(result))
    return result


def _get_volume_rank_naver() -> list[dict]:
    """네이버 금융 API 기반 거래량 상위 종목 조회 (폴백용)."""
    result = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for market in ("KOSPI", "KOSDAQ"):
        try:
            resp = requests.get(
                f"https://m.stock.naver.com/api/stocks/volume/{market}",
                params={"page": "1", "pageSize": "50"},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("네이버 %s 거래량 순위 조회 실패: %s", market, exc)
            continue

        for item in data.get("stocks", []):
            try:
                code = item.get("itemCode", "")
                if not code or len(code) != 6:
                    continue

                price_str = str(item.get("closePrice", "0")).replace(",", "")
                vol_str = str(item.get("accumulatedTradingVolume", "0")).replace(",", "")
                val_str = str(item.get("accumulatedTradingValue", "0")).replace(",", "")
                mcap_str = str(item.get("marketValue", "0")).replace(",", "")

                result.append({
                    "code": code,
                    "name": item.get("stockName", ""),
                    "price": int(price_str),
                    "change_rate": float(item.get("fluctuationsRatio", 0)),
                    "volume": int(vol_str),
                    "trade_amount": int(val_str) * 1_000_000,
                    "market_cap": int(mcap_str) * 100_000_000,
                })
            except (ValueError, TypeError):
                continue

    logger.info("네이버 거래량 순위: KOSPI+KOSDAQ %d종목", len(result))
    return result


# ══════════════════════════════════════════════════════════
# 시가총액 조회
# ══════════════════════════════════════════════════════════

def get_market_cap(code: str) -> int:
    """종목의 시가총액을 반환한다 (원 단위).

    현재가 조회의 시가총액 필드를 사용.
    에러 시 0 반환.
    """
    info = get_current_price(code)
    return info.get("market_cap", 0)
