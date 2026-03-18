"""KIS API 토큰 관리.

모의투자/실전투자 도메인 자동 전환.
토큰 캐싱 및 만료 60초 전 자동 갱신.
"""

import logging
import time

import requests

from app.config import APP_KEY, APP_SECRET, BASE_URL

logger = logging.getLogger(__name__)

# 글로벌 토큰 캐시
_access_token: str = ""
_token_expires_at: float = 0.0


def get_access_token() -> str:
    """OAuth 토큰 발급/캐시/자동갱신.

    만료 60초 전에 자동으로 재발급한다.
    """
    global _access_token, _token_expires_at

    # 유효 토큰이 있으면 캐시 반환
    if _access_token and time.time() < _token_expires_at - 60:
        return _access_token

    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }

    try:
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        _access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        _token_expires_at = time.time() + expires_in

        logger.info("토큰 발급 완료 (만료: %ds)", expires_in)
        return _access_token

    except requests.RequestException as exc:
        logger.error("토큰 발급 실패: %s", exc)
        raise
    except KeyError:
        logger.error("토큰 응답에 access_token 없음: %s", data)
        raise


def get_auth_headers() -> dict:
    """KIS API 공통 인증 헤더."""
    token = get_access_token()
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "custtype": "P",
    }


def invalidate_token():
    """토큰 캐시 강제 무효화 (에러 복구용)."""
    global _access_token, _token_expires_at
    _access_token = ""
    _token_expires_at = 0.0
    logger.info("토큰 캐시 무효화")
