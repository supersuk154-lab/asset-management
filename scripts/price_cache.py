"""
price_cache.py — yfinance 중앙 집중식 가격 캐시 (Phase 5-B)

모든 스크립트가 yfinance를 독립적으로 호출하는 대신 이 모듈을 통해
market_data/price_cache.json 에서 읽고, 7일 TTL이 지난 티커만 재수집한다.

캐시 구조:
  {
    "tickers": {
      "005930.KS": {
        "last_updated": "2026-06-02",
        "close": {"2025-06-02": 75000.0, ...},   // 1년치 일별 종가
        "info":  {"trailingPE": 15.2, "forwardPE": 12.1,
                  "dividendYield": 0.023, "totalAssets": null}
      }
    }
  }

공개 API:
  ensure_cached(tickers)          → 스테일 티커만 yfinance 배치 수집 후 저장
  get_close_series(ticker, cache) → {date_str: price} 또는 None
  get_info(ticker, cache)         → info dict 또는 None
  load()                          → 전체 캐시 dict 반환

사용 예:
  from price_cache import ensure_cached, get_close_series, get_info, load as load_cache
  ensure_cached(tickers)
  cache = load_cache()
  closes = get_close_series("005930.KS", cache)
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

_scripts_dir = Path(__file__).parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from utils import safe_write_json

BASE_DIR   = Path(__file__).parent.parent
MARKET_DIR = BASE_DIR / "market_data"
CACHE_FILE = MARKET_DIR / "price_cache.json"
CACHE_TTL_DAYS      = 7   # 가격(close) 캐시 TTL: 7일
CACHE_INFO_TTL_DAYS = 30  # 펀더멘털(info) 캐시 TTL: 30일 (느리게 변하므로 더 길게)
_INFO_MAX_WORKERS   = 5   # info 병렬 수집 스레드 수
TODAY = datetime.now().strftime("%Y-%m-%d")

_FMP_BASE = "https://financialmodelingprep.com"


# ─────────────────────────────────────
# .env 로더 (python-dotenv 없이 동작)
# ─────────────────────────────────────

def _load_env_once() -> None:
    """프로젝트 루트 .env 파일을 os.environ에 한 번만 로드한다."""
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


_load_env_once()


def _get_fmp_key() -> str | None:
    """FMP_API_KEY 환경 변수 반환. 없으면 None."""
    return os.environ.get("FMP_API_KEY") or None


# ─────────────────────────────────────
# FMP fallback — info 수집 (yfinance 실패 시)
# ─────────────────────────────────────

def _fetch_info_fmp(ticker: str, api_key: str) -> dict:
    """FMP API로 펀더멘털 info 수집 (yfinance 실패 시 fallback).

    호출 엔드포인트 (FMP stable API, 2025-09 이후 무료 지원):
      /stable/profile    → price, marketCap, lastDividend
      /stable/ratios-ttm → priceToEarningsRatioTTM

    반환 키: yfinance info_dict 와 동일한 4개 키
      trailingPE, forwardPE(항상 None), dividendYield, totalAssets

    FMP 무료 일일 한도: 250 req/day.
    이 함수는 2 req/ticker 소비하므로, fallback 티커 수가 100 이상이면
    일일 한도 소진 위험이 있다. 일반적으로 yfinance 부분 실패 시 사용되므로
    정상 운영에서는 극소수 티커에만 호출된다.
    """
    try:
        import requests as _req
    except ImportError:
        return {}

    info: dict = {"trailingPE": None, "forwardPE": None,
                  "dividendYield": None, "totalAssets": None}

    # 1) /stable/profile — price, marketCap, lastDividend
    try:
        r = _req.get(
            f"{_FMP_BASE}/stable/profile",
            params={"symbol": ticker, "apikey": api_key},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            p = data[0]
            price    = p.get("price")
            last_div = p.get("lastDividend")
            mkt_cap  = p.get("marketCap")
            # dividendYield = 연간 배당 / 현재가 (소수점 — yfinance 동일 단위)
            if last_div is not None and price and float(price) > 0:
                info["dividendYield"] = round(float(last_div) / float(price), 6)
            # totalAssets: ETF AUM 대체값으로 marketCap 사용
            if mkt_cap is not None:
                info["totalAssets"] = float(mkt_cap)
    except Exception as e:
        print(f"  [FMP] {ticker}: profile 호출 실패 — {str(e)[:60]}")

    # 2) /stable/ratios-ttm — priceToEarningsRatioTTM (Trailing PE)
    # KRX 티커(한국 주식)는 ratios-ttm이 유료 플랜 전용(402) → 스킵
    is_krx = ticker.endswith(".KS") or ticker.endswith(".KQ")
    if not is_krx:
        try:
            r2 = _req.get(
                f"{_FMP_BASE}/stable/ratios-ttm",
                params={"symbol": ticker, "apikey": api_key},
                timeout=8,
            )
            r2.raise_for_status()
            d2 = r2.json()
            row = d2[0] if isinstance(d2, list) and d2 else d2
            if isinstance(row, dict):
                pe = row.get("priceToEarningsRatioTTM")
                if pe is not None:
                    info["trailingPE"] = round(float(pe), 2)
        except Exception as e:
            print(f"  [FMP] {ticker}: ratios-ttm 호출 실패 — {str(e)[:60]}")

    return info


# ─────────────────────────────────────
# 캐시 I/O
# ─────────────────────────────────────

def load() -> dict:
    """전체 캐시 dict 반환. 파일 없으면 빈 dict."""
    if not CACHE_FILE.exists():
        return {"tickers": {}}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if "tickers" not in raw:
            return {"tickers": {}}
        return raw
    except Exception:
        return {"tickers": {}}


def _save(cache: dict) -> None:
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    safe_write_json(CACHE_FILE, cache)


def _is_stale(entry: dict, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    """last_updated 가 ttl_days 초과 또는 없으면 True."""
    last = entry.get("last_updated", "")
    if not last:
        return True
    try:
        return (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days >= ttl_days
    except Exception:
        return True


def _is_info_stale(entry: dict) -> bool:
    """info 캐시가 CACHE_INFO_TTL_DAYS 초과됐으면 True. info 키가 아예 없으면 True."""
    if not entry.get("info"):
        return True
    last = entry.get("info_updated") or entry.get("last_updated", "")
    if not last:
        return True
    try:
        return (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days >= CACHE_INFO_TTL_DAYS
    except Exception:
        return True


# ─────────────────────────────────────
# 공개 읽기 API
# ─────────────────────────────────────

def get_close_series(ticker: str, cache: dict | None = None) -> dict[str, float] | None:
    """
    ticker 의 일별 종가 dict ({date_str: price}) 반환.
    캐시에 없거나 빈 경우 None.
    """
    if cache is None:
        cache = load()
    entry = cache.get("tickers", {}).get(ticker)
    if not entry:
        return None
    closes = entry.get("close")
    return closes if closes else None


def get_info(ticker: str, cache: dict | None = None) -> dict | None:
    """ticker 의 펀더멘털 info dict 반환. 없으면 None."""
    if cache is None:
        cache = load()
    entry = cache.get("tickers", {}).get(ticker)
    if not entry:
        return None
    return entry.get("info") or None


# ─────────────────────────────────────
# 캐시 갱신 (yfinance 호출)
# ─────────────────────────────────────

def ensure_cached(tickers: list[str], period: str = "1y") -> dict:
    """
    tickers 중 스테일하거나 없는 종목만 yfinance 로 배치 수집하여 캐시에 저장.
    갱신된 전체 캐시 dict 를 반환한다.

    yfinance 미설치 시 경고만 출력하고 기존 캐시를 그대로 반환.
    """
    cache = load()
    tickers_cache = cache.setdefault("tickers", {})

    stale = [t for t in tickers if _is_stale(tickers_cache.get(t, {}))]
    if not stale:
        print(f"  [price_cache] 전체 {len(tickers)}개 캐시 유효 — yfinance 스킵")
        return cache

    print(f"  [price_cache] {len(stale)}/{len(tickers)}개 티커 갱신 필요")

    try:
        import yfinance as yf
    except ImportError:
        print("  ⚠  yfinance 없음 → pip install yfinance (기존 캐시 그대로 사용)")
        return cache

    # ── 1. 배치 Close 다운로드 (최대 3회 재시도, 지수 대기) ────
    single = len(stale) == 1
    hist = None
    _MAX_ATTEMPTS = 3
    for _attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            import time as _time
            hist = yf.download(
                stale if not single else stale[0],
                period=period,
                auto_adjust=True,
                progress=False,
                group_by="ticker" if not single else None,
            )
            break  # 성공
        except Exception as e:
            if _attempt == _MAX_ATTEMPTS:
                print(f"  ⚠ 배치 다운로드 최종 실패 ({_MAX_ATTEMPTS}회 시도): {e}")
                return cache
            wait = 2 ** _attempt  # 2s, 4s
            print(f"  ⚠ 배치 다운로드 실패 (시도 {_attempt}/{_MAX_ATTEMPTS}, {wait}초 후 재시도): {e}")
            _time.sleep(wait)

    if hist is None:
        return cache

    # ── 2. 티커별 종가 추출 ──────────────────────────────────
    close_results: dict[str, dict] = {}  # ticker → close_dict
    for ticker in stale:
        try:
            if single:
                close_raw = hist["Close"]
            else:
                close_raw = hist[ticker]["Close"]

            close_series = close_raw.dropna()
            if hasattr(close_series, "squeeze"):
                close_series = close_series.squeeze()

            if close_series.empty or len(close_series) < 5:
                print(f"  [price_cache] {ticker}: 종가 데이터 없음 — 스킵")
                continue

            close_results[ticker] = {
                str(idx.date()): round(float(val), 4)
                for idx, val in close_series.items()
            }

        except Exception as e:
            print(f"  [price_cache] {ticker}: 종가 추출 실패 — {str(e)[:60]}")

    # ── 3. info 병렬 수집 (TTL 30일 — 가격과 독립) ──────────
    # 가격 갱신 대상 + 기존 캐시 중 info가 스테일한 것 모두 포함
    info_needed = set(close_results.keys())
    for t in tickers:
        if t not in info_needed and _is_info_stale(tickers_cache.get(t, {})):
            info_needed.add(t)

    def _fetch_info(ticker: str) -> tuple[str, dict]:
        """단일 티커 info 수집. yfinance 실패 시 FMP fallback. (스레드 함수)"""
        info_dict: dict = {}
        try:
            raw_info = yf.Ticker(ticker).info
            for key in ("trailingPE", "forwardPE", "dividendYield", "totalAssets"):
                val = raw_info.get(key)
                info_dict[key] = float(val) if val is not None else None
        except Exception as fe:
            print(f"  [price_cache] {ticker}: yfinance info 실패 — {str(fe)[:60]}")

        # yfinance가 모든 필드를 None으로 반환했으면 FMP fallback 시도
        if not any(v is not None for v in info_dict.values()):
            fmp_key = _get_fmp_key()
            if fmp_key:
                print(f"  [price_cache] {ticker}: FMP fallback 시도...")
                fmp_info = _fetch_info_fmp(ticker, fmp_key)
                if any(v is not None for v in fmp_info.values()):
                    info_dict = fmp_info
                    pe_str  = fmp_info.get("trailingPE")
                    div_str = fmp_info.get("dividendYield")
                    print(f"  [FMP] {ticker}: fallback 성공 "
                          f"(PE={pe_str}, yield={div_str})")
        return ticker, info_dict

    info_results: dict[str, dict] = {}
    if info_needed:
        print(f"  [price_cache] info 병렬 수집: {len(info_needed)}개 티커 (max_workers={_INFO_MAX_WORKERS})")
        with ThreadPoolExecutor(max_workers=_INFO_MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_info, t): t for t in info_needed}
            for fut in as_completed(futures):
                t, info = fut.result()
                info_results[t] = info

    # ── 4. 캐시 병합 저장 ────────────────────────────────────
    for ticker, close_dict in close_results.items():
        existing = tickers_cache.get(ticker, {})
        tickers_cache[ticker] = {
            "last_updated": TODAY,
            "close":        close_dict,
            "info":         info_results.get(ticker, existing.get("info", {})),
            "info_updated": TODAY if ticker in info_results else existing.get("info_updated", ""),
        }
        print(f"  [price_cache] {ticker} — {len(close_dict)}일 종가 + info 저장")

    # 가격 갱신 없이 info만 갱신된 티커 처리
    for ticker, info_dict in info_results.items():
        if ticker not in close_results:
            existing = tickers_cache.get(ticker, {})
            existing["info"]         = info_dict
            existing["info_updated"] = TODAY
            tickers_cache[ticker]    = existing

    _save(cache)
    return cache
