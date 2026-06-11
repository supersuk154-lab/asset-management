"""
data_fetcher.py — 실시간 거시경제 API 및 주가 데이터 수집

역할: AI(LLM)가 직접 검색·계산하면 발생하는 수치 환각을 방지하기 위해,
     파이프라인 최상단에서 외부 API로 정확한 숫자를 확정하고 JSON으로 캐싱한다.

기능:
  1. FRED API (미국 연방준비제도) → 미국 기준금리(DFF), CPI(CPIAUCSL)
  2. ECOS API (한국은행) → 한국 기준금리, 소비자물가 전년비
  3. yfinance → trending_stocks.json 종목들의 52주 고점 대비 하락률 (배치 처리)

출력:
  market_data/realtime_macro_raw.json   ← macro-analyst가 읽어 해석만 수행
  market_data/trending_stocks.json      ← drawdown_pct 필드 추가 덮어씀 (stock-recommender용)

환경 변수 (선택):
  FRED_API_KEY : FRED API 키  → https://fred.stlouisfed.org/docs/api/api_key.html
  ECOS_API_KEY : ECOS API 키  → https://ecos.bok.or.kr/

  ※ API 키가 없으면 해당 항목은 fetch_status: "no_api_key"로 기록하고 건너뜀.
     파이프라인은 계속 진행 (macro-analyst가 일반 원칙으로 대체).

필요 패키지:
  pip install requests yfinance

사용법:
  python scripts/data_fetcher.py           # 오늘 데이터 없으면 실행
  python scripts/data_fetcher.py --force   # 강제 재실행
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils import safe_write_json
import price_cache as _pc

# ─────────────────────────────────────
# 선택 패키지 임포트 (없어도 파이프라인 계속 진행)
# ─────────────────────────────────────
try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _requests = None
    _REQUESTS_OK = False
    print("[Warning] requests 없음 → pip install requests (FRED/ECOS API 수집 불가)")

# Windows 콘솔 인코딩
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR     = Path(__file__).parent.parent
MARKET_DIR   = BASE_DIR / "market_data"
MACRO_RAW    = MARKET_DIR / "realtime_macro_raw.json"
TRENDING     = MARKET_DIR / "trending_stocks.json"
ETF_HOLDINGS = MARKET_DIR / "etf_holdings.json"
ETF_METRICS  = MARKET_DIR / "etf_metrics.json"

TODAY = datetime.now().strftime("%Y-%m-%d")

# ETF 청산 주의 AUM 임계값 (이 미만이면 소규모 → 청산 위험 플래그)
AUM_THRESHOLD = {
    "US": 100_000_000,       # 1억 달러
    "KR": 50_000_000_000,    # 500억 원
}


# ─────────────────────────────────────
# 캐시 확인
# ─────────────────────────────────────

def already_fetched_today() -> bool:
    """오늘 날짜 realtime_macro_raw.json이 이미 있으면 True"""
    if not MACRO_RAW.exists():
        return False
    try:
        data = json.loads(MACRO_RAW.read_text(encoding="utf-8"))
        return data.get("date") == TODAY
    except Exception:
        return False


# ─────────────────────────────────────
# .env 로더 (모듈 진입 시 1회)
# ─────────────────────────────────────

def _load_env() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env()


# ─────────────────────────────────────
# 1. FRED API — 미국 지표
# ─────────────────────────────────────

def _fetch_fred_latest(series_id: str, api_key: str) -> float | None:
    """FRED에서 특정 시리즈의 가장 최근 관측값 반환"""
    params = {
        "series_id":         series_id,
        "api_key":           api_key,
        "file_type":         "json",
        "sort_order":        "desc",
        "limit":             "3",
        "observation_start": (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d"),
    }
    resp = _requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    for obs in resp.json().get("observations", []):
        val = obs.get("value", ".")
        if val != ".":
            return float(val)
    return None


def get_us_indicators(api_key: str | None) -> dict:
    """미국 기준금리·CPI 수집"""
    result = {
        "us_fed_rate": {
            "value": None, "unit": "%",
            "label": "미국 연방기금금리 (Effective Federal Funds Rate)",
            "fetch_status": "",
        },
        "us_cpi": {
            "value": None, "unit": "index",
            "label": "미국 소비자물가지수 (CPI-U, 절대값)",
            "fetch_status": "",
        },
    }

    if not _REQUESTS_OK:
        for k in result:
            result[k]["fetch_status"] = "requests_not_installed"
        return result

    if not api_key:
        for k in result:
            result[k]["fetch_status"] = "no_api_key"
        return result

    for series_id, key in [("DFF", "us_fed_rate"), ("CPIAUCSL", "us_cpi")]:
        try:
            val = _fetch_fred_latest(series_id, api_key)
            result[key]["value"]        = val
            result[key]["fetch_status"] = "ok" if val is not None else "empty"
        except Exception as e:
            result[key]["fetch_status"] = f"error: {str(e)[:120]}"

    return result


# ─────────────────────────────────────
# 1-B. Alpha Vantage — 미국 지표 fallback (FRED 키 없을 때)
# ─────────────────────────────────────

_AV_BASE = "https://www.alphavantage.co/query"
_AV_FUNCTIONS = {
    "us_fed_rate": ("FEDERAL_FUNDS_RATE", "monthly"),
    "us_cpi":      ("CPI",                "monthly"),
}


def _fetch_av_latest(function: str, interval: str, api_key: str) -> float | None:
    """Alpha Vantage Economic Indicators API에서 최신 관측값 반환."""
    params = {"function": function, "interval": interval, "apikey": api_key}
    resp = _requests.get(_AV_BASE, params=params, timeout=10)
    resp.raise_for_status()
    data_list = resp.json().get("data", [])
    if not data_list:
        return None
    # data[0]이 가장 최신
    val = data_list[0].get("value", ".")
    return float(val) if val and val != "." else None


def get_us_indicators_av(api_key: str) -> dict:
    """Alpha Vantage로 미국 기준금리·CPI 수집 (FRED fallback).

    반환 구조는 get_us_indicators()와 동일하여 save_realtime_macro()에 그대로 전달 가능.
    """
    result = {
        "us_fed_rate": {
            "value": None, "unit": "%",
            "label": "미국 연방기금금리 (Alpha Vantage FEDERAL_FUNDS_RATE)",
            "fetch_status": "",
        },
        "us_cpi": {
            "value": None, "unit": "index",
            "label": "미국 소비자물가지수 (Alpha Vantage CPI)",
            "fetch_status": "",
        },
    }

    if not _REQUESTS_OK:
        for k in result:
            result[k]["fetch_status"] = "requests_not_installed"
        return result

    for key, (function, interval) in _AV_FUNCTIONS.items():
        try:
            val = _fetch_av_latest(function, interval, api_key)
            result[key]["value"]        = val
            result[key]["fetch_status"] = "ok_av" if val is not None else "empty_av"
        except Exception as e:
            result[key]["fetch_status"] = f"error_av: {str(e)[:120]}"

    return result


# ─────────────────────────────────────
# 2. ECOS API — 한국 지표
# ─────────────────────────────────────

def get_kor_indicators(api_key: str | None) -> dict:
    """한국 기준금리·소비자물가 수집 (한국은행 ECOS API)"""
    result = {
        "kor_base_rate": {
            "value": None, "unit": "%",
            "label": "한국 기준금리 (한국은행 통화정책)",
            "fetch_status": "",
        },
        "kor_cpi_yoy": {
            "value": None, "unit": "%",
            "label": "한국 소비자물가 전년동월비",
            "fetch_status": "",
        },
    }

    if not _REQUESTS_OK:
        for k in result:
            result[k]["fetch_status"] = "requests_not_installed"
        return result

    if not api_key:
        for k in result:
            result[k]["fetch_status"] = "no_api_key"
        return result

    # 최근 2개월 범위
    end_ym   = datetime.now().strftime("%Y%m")
    start_ym = (datetime.now() - timedelta(days=62)).strftime("%Y%m")

    # ① 기준금리: 통계표 722Y001, 항목 0101000, 월별
    try:
        url = (
            f"http://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr"
            f"/1/5/722Y001/MM/{start_ym}/{end_ym}/0101000"
        )
        resp = _requests.get(url, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("StatisticSearch", {}).get("row", [])
        if rows:
            val = rows[-1].get("DATA_VALUE")
            result["kor_base_rate"]["value"]        = float(val) if val else None
            result["kor_base_rate"]["fetch_status"] = "ok"
        else:
            result["kor_base_rate"]["fetch_status"] = "empty"
    except Exception as e:
        result["kor_base_rate"]["fetch_status"] = f"error: {str(e)[:120]}"

    # ② 소비자물가 전년비: 통계표 021Y125, 항목 0 (전체), 월별
    try:
        url = (
            f"http://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr"
            f"/1/5/021Y125/MM/{start_ym}/{end_ym}/0"
        )
        resp = _requests.get(url, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("StatisticSearch", {}).get("row", [])
        if rows:
            val = rows[-1].get("DATA_VALUE")
            result["kor_cpi_yoy"]["value"]        = float(val) if val else None
            result["kor_cpi_yoy"]["fetch_status"] = "ok"
        else:
            result["kor_cpi_yoy"]["fetch_status"] = "empty"
    except Exception as e:
        result["kor_cpi_yoy"]["fetch_status"] = f"error: {str(e)[:120]}"

    return result


# ─────────────────────────────────────
# 3. yfinance — 52주 고점 대비 하락률 (배치 처리)
# ─────────────────────────────────────

# ticker_normalizer 표준 사전 참조 (단일 사전 관리 원칙 — DUP-02 수정)
try:
    _scripts_dir = Path(__file__).parent
    import sys as _sys
    if str(_scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(_scripts_dir))
    from ticker_normalizer import TICKER_DICT as _NORM_DICT, _normalize_key as _norm_key
    _NORM_OK = True
except ImportError:
    _NORM_DICT: dict = {}
    _norm_key = None
    _NORM_OK = False

# 보조 매핑 — ticker_normalizer에 없거나 임포트 실패 시 fallback
KOR_NAME_TO_TICKER: dict[str, str] = {
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "현대차": "005380.KS",
    "기아": "000270.KS", "LG에너지솔루션": "373220.KS", "삼성바이오로직스": "207940.KS",
    "셀트리온": "068270.KS", "POSCO홀딩스": "005490.KS", "카카오": "035720.KS",
    "네이버": "035420.KS", "NAVER": "035420.KS", "LG화학": "051910.KS",
    "현대모비스": "012330.KS", "삼성SDI": "006400.KS", "KB금융": "105560.KS",
    "신한지주": "055550.KS", "하나금융지주": "086790.KS", "우리금융지주": "316140.KS",
    "크래프톤": "259960.KS", "한국전력": "015760.KS", "두산에너빌리티": "034020.KS",
    "HD현대중공업": "329180.KS", "한화에어로스페이스": "012450.KS",
    "HD한국조선해양": "009540.KS", "LS ELECTRIC": "010120.KS",
    "테슬라": "TSLA", "엔비디아": "NVDA", "애플": "AAPL",
    "마이크로소프트": "MSFT", "구글": "GOOGL", "알파벳": "GOOGL",
    "메타": "META", "아마존": "AMZN", "팔란티어": "PLTR", "AMD": "AMD",
}


def _resolve_ticker(name: str, hint: str = "") -> str | None:
    """종목명 → yfinance 티커 변환.
    hint(숫자 6자리)가 있으면 .KS로 변환해 우선 사용.
    ticker_normalizer TICKER_DICT를 1차 조회, 실패 시 KOR_NAME_TO_TICKER 폴백.
    """
    if hint:
        cleaned = hint.strip()
        if cleaned.isdigit() and len(cleaned) == 6:
            return f"{cleaned}.KS"
        if cleaned:
            return cleaned
    if _NORM_OK and _norm_key is not None:
        nk = _norm_key(name)
        for info in _NORM_DICT.values():
            if nk in [_norm_key(a) for a in info.get("aliases", [])]:
                return info["ticker"]
    return KOR_NAME_TO_TICKER.get(name)


def enrich_trending_with_drawdown() -> None:
    """trending_stocks.json 각 종목에 drawdown_pct 필드를 추가하여 덮어쓴다.
    price_cache 경유 — 7일 TTL 내 캐시 적중 시 yfinance 호출 생략.
    """
    if not TRENDING.exists():
        print("  [스킵] trending_stocks.json 없음 — fetch_market_data.py를 먼저 실행하세요")
        return

    data   = json.loads(TRENDING.read_text(encoding="utf-8"))
    stocks = data.get("stocks", [])

    # ── 티커 수집 ─────────────────────────────────────────────
    ticker_map: dict[str, list] = {}   # ticker → [stock_entry, ...]
    for stock in stocks:
        name   = stock.get("name", "")
        hint   = stock.get("ticker", "")
        ticker = _resolve_ticker(name, hint)
        if ticker:
            ticker_map.setdefault(ticker, []).append(stock)
        else:
            stock.update({"drawdown_pct": None, "fetch_status": "no_ticker_mapping"})
            print(f"  {name}: 티커 매핑 없음 → drawdown_pct = null")

    if not ticker_map:
        safe_write_json(TRENDING, data)
        print("  [완료] 갱신 가능한 티커 없음")
        return

    all_tickers = list(ticker_map.keys())
    cache = _pc.ensure_cached(all_tickers)

    # ── 드로우다운 + 펀더멘털 계산 ────────────────────────────
    updated = 0
    for ticker, stock_list in ticker_map.items():
        close_dict = _pc.get_close_series(ticker, cache)
        if not close_dict:
            # KIS fallback (국내 종목)
            if ticker.endswith(".KS"):
                kr = _kis_drawdown_fallback(ticker)
                if kr:
                    for s in stock_list:
                        s.update({**kr, "fetch_status": "ok_kis_fallback", "ticker_used": ticker})
                    updated += 1
                    continue
            for s in stock_list:
                s.update({"drawdown_pct": None, "fetch_status": "empty", "ticker_used": ticker})
            print(f"  {ticker}: 데이터 없음 (상장 폐지 또는 티커 오류)")
            continue

        try:
            prices   = list(close_dict.values())
            current  = round(prices[-1], 2)
            high_52w = round(max(prices), 2)
            drop     = round((current - high_52w) / high_52w * 100, 1)

            info_raw       = _pc.get_info(ticker, cache) or {}
            raw_pe         = info_raw.get("trailingPE")
            raw_fpe        = info_raw.get("forwardPE")
            raw_div        = info_raw.get("dividendYield")
            trailing_pe    = round(float(raw_pe),  1) if raw_pe  is not None else None
            forward_pe     = round(float(raw_fpe), 1) if raw_fpe is not None else None
            dividend_yield = round(float(raw_div), 2) if raw_div is not None else None

            for s in stock_list:
                s.update({
                    "current_price":  current,
                    "high_52w":       high_52w,
                    "drawdown_pct":   drop,
                    "trailing_pe":    trailing_pe,
                    "forward_pe":     forward_pe,
                    "dividend_yield": dividend_yield,
                    "fetch_status":   "ok",
                    "ticker_used":    ticker,
                })
            updated += 1
            pe_str  = f"PER {trailing_pe}" if trailing_pe else "PER N/A"
            div_str = f"배당 {dividend_yield}%" if dividend_yield else ""
            print(f"  {stock_list[0].get('name', ticker)} ({ticker}): "
                  f"현재가 {current:,.0f} / 52주 고점 {high_52w:,.0f} / 하락률 {drop}% / {pe_str} {div_str}")

        except Exception as e:
            for s in stock_list:
                s.update({"drawdown_pct": None,
                          "fetch_status": f"error: {str(e)[:80]}",
                          "ticker_used":  ticker})
            print(f"  {ticker}: 계산 실패 — {str(e)[:60]}")

    safe_write_json(TRENDING, data)
    print(f"  [OK] trending_stocks.json drawdown_pct 업데이트 — {updated}개 티커")


# ─────────────────────────────────────
# KIS Open API fallback (국내 .KS 종목 낙폭 보강)
# ─────────────────────────────────────

_KIS_CHECKED = False
_KIS_OK      = False

def _kis_drawdown_fallback(ticker: str) -> dict | None:
    """yfinance가 국내 종목 낙폭 조회에 실패할 때 KIS Open API로 보강.
    KIS 키가 없으면(또는 모듈 없으면) 조용히 None 반환."""
    global _KIS_CHECKED, _KIS_OK
    try:
        import kis_client
    except ImportError:
        return None
    if not _KIS_CHECKED:
        _KIS_OK = kis_client.is_available()
        _KIS_CHECKED = True
        if _KIS_OK:
            print("  [KIS] 국내 종목 fallback 활성 (yfinance 실패 시 KIS 사용)")
    if not _KIS_OK:
        return None
    result = kis_client.get_daily_drawdown(ticker)
    if result:
        print(f"  [KIS] {ticker} 낙폭 보강: {result['drawdown_pct']}%")
    return result


# ─────────────────────────────────────
# 4. ETF 메트릭 — 낙폭·순자산(AUM)·청산위험 (etf_metrics.json)
# ─────────────────────────────────────

def enrich_etf_metrics() -> None:
    """
    etf_holdings.json의 ETF 티커들을 yfinance로 조회하여
    낙폭(drawdown_pct)·순자산(aum)·청산위험 플래그를 계산하고
    expense_ratio_pct(정적)와 합쳐 etf_metrics.json에 저장한다.

    stock-recommender가 이 한 파일로 ETF 비용·낙폭·청산위험을 모두 참조한다.
    """
    if not ETF_HOLDINGS.exists():
        print("  [스킵] etf_holdings.json 없음 — ETF 메트릭 생략")
        return

    try:
        etf_db = json.loads(ETF_HOLDINGS.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [스킵] etf_holdings.json 로드 실패: {e}")
        return

    # 메타(_ 로 시작하는 키) 제외하고 실제 ETF 티커만
    tickers = [k for k in etf_db.keys() if not k.startswith("_")]
    if not tickers:
        print("  [스킵] etf_holdings.json에 ETF 없음")
        return

    cache = _pc.ensure_cached(tickers)

    metrics = {}
    for ticker in tickers:
        info_meta = etf_db[ticker]
        market    = info_meta.get("market", "")
        entry = {
            "name":              info_meta.get("name", ticker),
            "market":            market,
            "index":             info_meta.get("index", ""),
            "expense_ratio_pct": info_meta.get("expense_ratio_pct"),
            "current_price":     None,
            "drawdown_pct":      None,
            "aum":               None,
            "liquidation_risk":  None,
            "fetch_status":      "",
        }

        # 낙폭 (price_cache 경유)
        close_dict = _pc.get_close_series(ticker, cache)
        if close_dict:
            try:
                prices   = list(close_dict.values())
                current  = round(prices[-1], 2)
                high_52w = round(max(prices), 2)
                entry["current_price"] = current
                entry["drawdown_pct"]  = round((current - high_52w) / high_52w * 100, 1)
                entry["fetch_status"]  = "ok"
            except Exception as e:
                entry["fetch_status"] = f"price_error: {str(e)[:50]}"
        else:
            entry["fetch_status"] = "empty"

        # KIS fallback: 국내(.KS) 종목이고 캐시 낙폭 없을 시
        if entry["drawdown_pct"] is None and ticker.endswith(".KS"):
            kr = _kis_drawdown_fallback(ticker)
            if kr:
                entry["current_price"] = kr["current_price"]
                entry["drawdown_pct"]  = kr["drawdown_pct"]
                entry["fetch_status"]  = "ok_kis_fallback"

        # 순자산(AUM) + 청산위험 (price_cache info 경유)
        info_raw = _pc.get_info(ticker, cache) or {}
        aum = info_raw.get("totalAssets")
        if aum:
            entry["aum"] = int(aum)
            threshold = AUM_THRESHOLD.get(market)
            if threshold is not None:
                entry["liquidation_risk"] = bool(aum < threshold)

        metrics[ticker] = entry

        dd_str  = f"{entry['drawdown_pct']}%" if entry["drawdown_pct"] is not None else "N/A"
        ter_str = f"TER {entry['expense_ratio_pct']}%" if entry["expense_ratio_pct"] is not None else ""
        risk_str = " ⚠️청산주의" if entry["liquidation_risk"] else ""
        print(f"  {entry['name']} ({ticker}): 낙폭 {dd_str} / {ter_str}{risk_str}")

    _write_etf_metrics(metrics)


def _save_etf_metrics_static(etf_db: dict, tickers: list) -> None:
    """yfinance 불가 시 expense_ratio 정적값만이라도 저장."""
    metrics = {}
    for ticker in tickers:
        meta = etf_db[ticker]
        metrics[ticker] = {
            "name":              meta.get("name", ticker),
            "market":            meta.get("market", ""),
            "index":             meta.get("index", ""),
            "expense_ratio_pct": meta.get("expense_ratio_pct"),
            "current_price":     None,
            "drawdown_pct":      None,
            "aum":               None,
            "liquidation_risk":  None,
            "fetch_status":      "no_yfinance",
        }
    _write_etf_metrics(metrics)


def _write_etf_metrics(metrics: dict) -> None:
    MARKET_DIR.mkdir(exist_ok=True)
    payload = {
        "date":    TODAY,
        "source":  "yfinance + etf_holdings.json",
        "note":    ("expense_ratio_pct는 참고용 근사치(운용사 공시 확인 필요). "
                    "liquidation_risk=true는 순자산(AUM)이 임계값 미만으로 청산 위험이 상대적으로 높음을 의미. "
                    "drawdown_pct는 52주 고점 대비 하락률."),
        "etfs":    metrics,
    }
    safe_write_json(ETF_METRICS, payload)
    n_risk = sum(1 for m in metrics.values() if m.get("liquidation_risk"))
    print(f"  [OK] etf_metrics.json 저장 — {len(metrics)}개 ETF (청산주의 {n_risk}개)")


# ─────────────────────────────────────
# realtime_macro_raw.json 저장
# ─────────────────────────────────────

def save_realtime_macro(us: dict, kor: dict, errors: list) -> None:
    MARKET_DIR.mkdir(exist_ok=True)
    payload = {
        "date":         TODAY,
        "source":       "data_fetcher_api",
        "fetch_errors": errors,
        **us,
        **kor,
    }
    safe_write_json(MACRO_RAW, payload)
    print(f"  [OK] realtime_macro_raw.json 저장 완료")


# ─────────────────────────────────────
# 메인
# ─────────────────────────────────────

def run(force: bool = False) -> None:
    if not force and already_fetched_today():
        print(f"[스킵] 오늘({TODAY}) realtime_macro_raw.json 이미 존재."
              " --force로 강제 재실행 가능.")
        return

    print(f"[data_fetcher] {TODAY} 실시간 데이터 수집 시작\n")

    fred_key = os.getenv("FRED_API_KEY")
    ecos_key = os.getenv("ECOS_API_KEY")
    av_key   = os.getenv("ALPHA_VANTAGE_API_KEY")

    if not fred_key:
        if av_key:
            print("  ℹ  FRED_API_KEY 없음 → Alpha Vantage로 미국 지표 수집")
        else:
            print("  ⚠  FRED_API_KEY·ALPHA_VANTAGE_API_KEY 모두 없음 → 미국 지표 수집 건너뜀"
                  " (macro-analyst가 일반 원칙으로 대체)")
    if not ecos_key:
        print("  ⚠  ECOS_API_KEY 없음 → 한국 지표 수집 건너뜀"
              " (macro-analyst가 일반 원칙으로 대체)")

    errors: list[str] = []

    # ── 1. 미국 지표 (FRED 우선, 없으면 Alpha Vantage) ──────
    if fred_key:
        print("\n[1단계] 미국 지표 수집 (FRED API)")
        us_data = get_us_indicators(fred_key)
    elif av_key:
        print("\n[1단계] 미국 지표 수집 (Alpha Vantage — FRED fallback)")
        us_data = get_us_indicators_av(av_key)
    else:
        print("\n[1단계] 미국 지표 수집 건너뜀 (API 키 없음)")
        us_data = get_us_indicators(None)  # no_api_key 상태로 구성

    for key, val in us_data.items():
        st = val.get("fetch_status", "")
        if st.startswith("error"):
            errors.append(f"{key}: {st}")

    # ── 2. 한국 지표 ─────────────────────────────
    print("[2단계] 한국 지표 수집 (ECOS API)")
    kor_data = get_kor_indicators(ecos_key)
    for key, val in kor_data.items():
        st = val.get("fetch_status", "")
        if st.startswith("error"):
            errors.append(f"{key}: {st}")

    # ── 3. 거시경제 수치 저장 ─────────────────────
    save_realtime_macro(us_data, kor_data, errors)

    # ── 4. 주가 하락률 계산 (yfinance 배치) ──────
    print("\n[3단계] 주가 52주 고점 대비 하락률 계산 (yfinance 배치)")
    enrich_trending_with_drawdown()

    # ── 5. ETF 메트릭 (낙폭·AUM·청산위험) ────────
    print("\n[4단계] ETF 메트릭 수집 (낙폭·순자산·비용)")
    enrich_etf_metrics()

    # ── 최종 요약 ─────────────────────────────────
    print(f"\n[완료] data_fetcher.py 완료 / API 오류 {len(errors)}건")
    if errors:
        for e in errors:
            print(f"  ⚠ {e}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(force=force)
