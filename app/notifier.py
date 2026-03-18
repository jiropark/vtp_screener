"""텔레그램 봇 알림 모듈 (VTP 스크리너).

모든 알림은 HTML 형식으로 전송.
전송 실패 시 로그만 남기고 예외를 발생시키지 않는다.
"""

import logging

import requests

from app.config import TG_BOT_TOKEN, TG_CHAT_ID

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str):
    """텔레그램 메시지 전송 (내부용)."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.debug("텔레그램 토큰/채팅ID 미설정, 알림 스킵")
        return

    try:
        resp = requests.post(
            API_URL.format(token=TG_BOT_TOKEN),
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if not resp.json().get("ok"):
            logger.warning("텔레그램 전송 실패: %s", resp.text)
    except Exception:
        logger.exception("텔레그램 전송 에러")


def notify_signal(code: str, name: str, score: float, details: dict | None = None):
    """시그널 감지 알림.

    Args:
        details: {volume_score, price_score, supply_score, volume_ratio, atr, ...}
    """
    lines = [
        f"<b>🔍 [VTP] 시그널 감지</b>",
        f"종목: {name} ({code})",
        f"총점: <b>{score:.1f}점</b>",
    ]
    if details:
        if "volume_score" in details:
            lines.append(f"  거래량: {details['volume_score']:.1f}")
        if "price_score" in details:
            lines.append(f"  가격: {details['price_score']:.1f}")
        if "supply_score" in details:
            lines.append(f"  수급: {details['supply_score']:.1f}")
        if "volume_ratio" in details:
            lines.append(f"  거래량비: {details['volume_ratio']:.1f}배")
        if "close_vs_high" in details:
            lines.append(f"  종가/고가: {details['close_vs_high']:.1%}")
    _send("\n".join(lines))


def notify_buy(code: str, name: str, price: int, qty: int,
               score: float = 0, gap_scenario: str = ""):
    """매수 체결 알림."""
    amount = price * qty
    lines = [
        f"<b>📈 [VTP] 매수</b>",
        f"종목: {name} ({code})",
        f"수량: {qty}주 @ {price:,}원",
        f"금액: {amount:,}원",
    ]
    if score:
        lines.append(f"스코어: {score:.1f}점")
    if gap_scenario:
        lines.append(f"시나리오: {gap_scenario}")
    _send("\n".join(lines))


def notify_sell(code: str, name: str, price: int, qty: int,
                pnl_pct: float = 0, reason: str = ""):
    """매도 체결 알림."""
    emoji = "📉" if pnl_pct < 0 else "📊"
    amount = price * qty
    lines = [
        f"<b>{emoji} [VTP] 매도</b>",
        f"종목: {name} ({code})",
        f"수량: {qty}주 @ {price:,}원",
        f"금액: {amount:,}원",
        f"손익: <b>{pnl_pct:+.2f}%</b>",
    ]
    if reason:
        lines.append(f"사유: {reason}")
    _send("\n".join(lines))


def notify_daily_report(summary: dict):
    """일일 리포트 알림.

    Args:
        summary: {total_asset, cash, stock_value, daily_return_pct,
                  total_return_pct, position_count, signals_count,
                  trades_count, positions: [{name, pnl_pct}, ...]}
    """
    total = summary.get("total_asset", 0)
    cash = summary.get("cash", 0)
    stock = summary.get("stock_value", 0)
    daily_pct = summary.get("daily_return_pct", 0)
    total_pct = summary.get("total_return_pct", 0)

    lines = [
        f"<b>📋 [VTP] 일일 리포트</b>",
        f"",
        f"💰 총자산: {total:,}원",
        f"  일일: {daily_pct:+.2f}% / 누적: {total_pct:+.2f}%",
        f"  현금: {cash:,}원 / 주식: {stock:,}원",
        f"",
        f"📊 시그널: {summary.get('signals_count', 0)}건",
        f"📊 거래: {summary.get('trades_count', 0)}건",
    ]

    positions = summary.get("positions", [])
    if positions:
        lines.append(f"")
        lines.append(f"<b>📌 보유종목 ({len(positions)})</b>")
        for p in positions:
            pnl = p.get("pnl_pct", 0)
            lines.append(f"  {p.get('name', '?')}: {pnl:+.1f}%")

    _send("\n".join(lines))


def notify_risk_alert(message: str):
    """리스크 경고 알림."""
    _send(f"<b>⚠️ [VTP] 리스크 경고</b>\n\n{message}")


def notify_error(context: str, error: str):
    """에러 알림."""
    _send(f"<b>🚨 [VTP] 에러</b>\n\n{context}\n{error}")
