"""
kis_client.py — 한국투자증권(KIS) Open API 클라이언트 (국내 ETF 시세 fallback)

목적: yfinance가 국내 종목(.KS)의 가격/낙폭 조회에 실패하거나 불안정할 때,
     KIS Open API로 국내 ETF의 일별 시세를 가져와 drawdown_pct를 보강한다.

환경 변수 (선택):
  KIS_APP_KEY    : KIS Open API 앱키     → https://apiportal.koreainvestment.com/
  KIS_APP_SECRET : KIS Open API 앱시크릿

  ※ 키가 없으면 is_available()이 False를 반환하고, data_fetcher는 KIS 호출을 건너뛴다.
     (FRED/ECOS와 동일한 graceful-skip 패턴)

토큰 캐싱: market_data/kis_token.json (발급 제한 1분 1회 → 24h 토큰 재사용 필수)

필요 패키지: requests
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _requests = None
    _REQUESTS_OK = False

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR    = Path(__file__).parent.parent
MARKET_DIR  = BASE_DIR / "market_data"
TOKEN_CACHE = MARKET_DIR / "kis_token.json"

# secrets.env에서 KIS 키 로드 (python-dotenv 있으면 자동, 없으면 OS 환경변수만 사용)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / "secrets.env")
except ImportError:
    pass

# 실전 투자 도메인 (시세 조회용)
KIS_BASE = "https://openapi.koreainvestment.com:9443"


def is_available() -> bool:
    """KIS 키와 requests가 모두 준비됐는지."""
    return _REQUESTS_OK and bool(os.getenv("KIS_APP_KEY")) and bool(os.getenv("KIS_APP_SECRET"))


def _get_token() -> str | None:
    """캐시된 토큰이 유효하면 재사용, 아니면 발급. 실패 시 None."""
    if not is_available():
        return None

    # 캐시 확인
    if TOKEN_CACHE.exists():
        try:
            cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            expires_at = cache.get("expires_at", 0)
            # 만료 10분 전까지 유효로 간주
            if datetime.now().timestamp() < expires_at - 600:
                return cache.get("access_token")
        except Exception:
            pass

    # 신규 발급
    try:
        resp = _requests.post(
            f"{KIS_BASE}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey":     os.getenv("KIS_APP_KEY"),
                "appsecret":  os.getenv("KIS_APP_SECRET"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data       = resp.json()
        token      = data.get("access_token")
        expires_in = int(data.get("expires_in", 86400))
        if not token:
            return None
        MARKET_DIR.mkdir(exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": token,
            "expires_at":   datetime.now().timestamp() + expires_in,
            "issued_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return token
    except Exception as e:
        print(f"  [KIS] 토큰 발급 실패: {str(e)[:80]}")
        return None


def _normalize_code(ticker: str) -> str:
    """'273130.KS' → '273130' (KIS는 6자리 종목코드만 사용)."""
    return ticker.split(".")[0].strip()


def get_daily_drawdown(ticker: str, lookback_days: int = 365) -> dict | None:
    """
    KIS 일별 시세로 52주 고점 대비 낙폭 계산.

    Returns:
        {"current_price": float, "high_52w": float, "drawdown_pct": float} 또는 None
    """
    token = _get_token()
    if not token:
        return None

    code = _normalize_code(ticker)
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        os.getenv("KIS_APP_KEY"),
        "appsecret":     os.getenv("KIS_APP_SECRET"),
        "tr_id":         "FHKST03010100",   # 국내주식 기간별 시세(일/주/월)
        "custtype":      "P",
    }
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

    # 한 번에 최대 ~100건 → lookback 구간을 140일씩 나눠 페이징
    collected: dict[str, float] = {}   # {date_str: close}
    end = datetime.now()
    segments = max(1, lookback_days // 140 + 1)

    try:
        for _ in range(segments):
            start = end - timedelta(days=140)
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",      # 주식/ETF
                "FID_INPUT_ISCD":         code,
                "FID_INPUT_DATE_1":       start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2":       end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE":    "D",       # 일봉
                "FID_ORG_ADJ_PRC":        "0",       # 수정주가
            }
            resp = _requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            rows = resp.json().get("output2", []) or []
            for r in rows:
                d = r.get("stck_bsop_date")
                c = r.get("stck_clpr")
                if d and c:
                    try:
                        collected[d] = float(c)
                    except ValueError:
                        pass
            end = start - timedelta(days=1)

        if not collected:
            return None

        dates    = sorted(collected.keys())
        current  = collected[dates[-1]]
        high_52w = max(collected.values())
        if high_52w <= 0:
            return None
        drawdown = round((current - high_52w) / high_52w * 100, 1)
        return {
            "current_price": round(current, 2),
            "high_52w":      round(high_52w, 2),
            "drawdown_pct":  drawdown,
        }
    except Exception as e:
        print(f"  [KIS] {code} 시세 조회 실패: {str(e)[:80]}")
        return None


def search_ticker_by_name(name: str) -> dict | None:
    """
    종목명(한글)으로 KIS API를 검색하여 6자리 종목코드와 ticker 반환.
    ticker_normalizer의 unresolved 종목 자동 해소용 fallback.

    Returns:
        {"code": "XXXXXX", "ticker": "XXXXXX.KS", "standard_name": "..."} 또는 None
    """
    token = _get_token()
    if not token:
        return None

    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        os.getenv("KIS_APP_KEY"),
        "appsecret":     os.getenv("KIS_APP_SECRET"),
        "tr_id":         "CTCA0002R",
        "custtype":      "P",
    }
    url    = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/search-stock-info"
    params = {
        "AUTH":    "",
        "EXCH_CD": "KNX",   # KRX 전체 (KOSPI + KOSDAQ)
        "KEYWORD": name,
    }

    try:
        resp = _requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data   = resp.json()
        output = data.get("output", [])
        if not output:
            print(f"  [KIS] '{name}' — 검색 결과 없음")
            return None

        # 이름에 검색어가 포함된 항목 우선, 없으면 첫 번째 결과
        best = next(
            (item for item in output if name in item.get("prdt_name", "")),
            output[0],
        )
        code     = best.get("shtn_pdno") or best.get("pdno", "")
        std_name = best.get("prdt_name") or name
        if not code:
            print(f"  [KIS] '{name}' — 종목코드 파싱 실패 (응답: {best})")
            return None

        print(f"  [KIS] '{name}' → {std_name} ({code}.KS)")
        return {
            "code":          code,
            "ticker":        f"{code}.KS",
            "standard_name": std_name,
        }
    except Exception as e:
        print(f"  [KIS] '{name}' 종목명 검색 실패: {str(e)[:120]}")
        return None


if __name__ == "__main__":
    # 단독 실행: 키가 있으면 샘플 종목 조회 테스트
    print(f"KIS 사용 가능: {is_available()}")
    if is_available():
        for t in ["273130.KS", "148070.KS"]:
            r = get_daily_drawdown(t)
            print(f"  {t}: {r}")
        # 종목명 검색 테스트
        for n in ["KODEX 코리아소버린AI", "KoAct 글로벌AI메모리반도체액티브"]:
            r = search_ticker_by_name(n)
            print(f"  검색 '{n}': {r}")
    else:
        print("  KIS_APP_KEY/KIS_APP_SECRET 환경변수가 없어 비활성 상태입니다.")
        print("  키를 설정하면 국내 ETF 시세 fallback이 자동 활성화됩니다.")
