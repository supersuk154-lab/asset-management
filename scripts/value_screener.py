"""
value_screener.py — 가치투자 관점 역발상 종목 스크리너

trending_stocks.json에 없거나 주목도가 낮지만 PER·배당·낙폭 기준으로
저평가된 종목을 발굴하여 stock-recommender가 역발상 후보로 참고하도록 한다.

선정 기준 (AND 아닌 OR 조합):
  A. 52주 고점 대비 낙폭 ≤ -15% (시장에서 소외된 구간)
  B. trailing_pe < 섹터 기준값 (PER 저평가)
  C. dividend_yield ≥ 2.0% (인컴 가치)

역발상 가점:
  trending_stocks.json에 없거나 mention_count < 2 → contrarian: true

출력: market_data/value_picks.json

사용법:
  python scripts/value_screener.py           # 오늘 캐시 없으면 실행
  python scripts/value_screener.py --force   # 강제 재실행
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from utils import safe_write_json
import price_cache as _pc

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR   = Path(__file__).parent.parent
MARKET_DIR = BASE_DIR / "market_data"
TRENDING   = MARKET_DIR / "trending_stocks.json"
OUTPUT     = MARKET_DIR / "value_picks.json"
TODAY      = datetime.now().strftime("%Y-%m-%d")

_SCREENER_CONFIG_PATH = MARKET_DIR / "screener_config.json"


def _load_screener_config() -> dict:
    """screener_config.json에서 UNIVERSE와 임계값 로드. 파일 없으면 FileNotFoundError."""
    if not _SCREENER_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"screener_config.json 없음: {_SCREENER_CONFIG_PATH}\n"
            "market_data/screener_config.json 파일이 필요합니다."
        )
    return json.loads(_SCREENER_CONFIG_PATH.read_text(encoding="utf-8"))


# 지연 로딩(Lazy Loading) — 모듈 임포트 시점이 아닌 첫 사용 시점에 파일을 읽는다.
# 모듈 로드 시 screener_config.json이 없어도 ImportError가 발생하지 않는다.
_cfg_cache: dict | None = None


def _get_config() -> dict:
    """설정을 캐싱하여 반환. 첫 호출 시 screener_config.json을 로드한다."""
    global _cfg_cache
    if _cfg_cache is None:
        _cfg_cache = _load_screener_config()
    return _cfg_cache


def load_trending_names() -> dict[str, int]:
    """trending_stocks.json에서 종목명 → mention_count 매핑 반환"""
    if not TRENDING.exists():
        return {}
    try:
        data   = json.loads(TRENDING.read_text(encoding="utf-8"))
        return {s["name"]: s.get("mention_count", 0) for s in data.get("stocks", [])}
    except Exception:
        return {}


def already_screened_recently() -> bool:
    """최근 cache_days일 이내에 스크리닝한 결과가 있으면 True"""
    if not OUTPUT.exists():
        return False
    try:
        cache_days = _get_config().get("cache_days", 7)
        data       = json.loads(OUTPUT.read_text(encoding="utf-8"))
        last_date  = data.get("date", "")
        if not last_date:
            return False
        last_dt = datetime.strptime(last_date, "%Y-%m-%d")
        now_dt  = datetime.strptime(TODAY, "%Y-%m-%d")
        return (now_dt - last_dt).days < cache_days
    except Exception:
        return False


def screen() -> list[dict]:
    cfg              = _get_config()
    universe         = cfg["universe"]
    drawdown_thresh  = cfg.get("drawdown_threshold", -15.0)
    dividend_thresh  = cfg.get("dividend_threshold", 2.0)

    trending_map = load_trending_names()
    picks = []

    tickers = list(universe.keys())
    cache = _pc.ensure_cached(tickers)

    for ticker, meta in universe.items():
        name         = meta["name"]
        pe_threshold = meta["pe_threshold"]
        div_req      = meta.get("div_threshold", 2.0)

        # ── 52주 낙폭 (price_cache 경유) ────────────────────────
        drawdown_pct  = None
        current_price = None
        close_dict = _pc.get_close_series(ticker, cache)
        if close_dict:
            try:
                prices        = list(close_dict.values())
                current_price = round(prices[-1], 2)
                high_52w      = round(max(prices), 2)
                drawdown_pct  = round((current_price - high_52w) / high_52w * 100, 1)
            except Exception as e:
                print(f"  [경고] {name} ({ticker}): 낙폭 계산 실패 — {e}")

        # ── 펀더멘털 (price_cache info 경유) ────────────────────
        trailing_pe    = None
        forward_pe     = None
        dividend_yield = None
        info_raw = _pc.get_info(ticker, cache) or {}
        try:
            raw_pe         = info_raw.get("trailingPE")
            raw_fpe        = info_raw.get("forwardPE")
            raw_div        = info_raw.get("dividendYield")
            trailing_pe    = round(float(raw_pe),  1) if raw_pe  is not None else None
            forward_pe     = round(float(raw_fpe), 1) if raw_fpe is not None else None
            dividend_yield = round(float(raw_div), 2) if raw_div is not None else None
        except Exception as e:
            print(f"  [경고] {name} ({ticker}): 펀더멘털 파싱 실패 — {e}")

        # ── 선정 기준 평가 ──────────────────────────────────────
        reasons = []

        if drawdown_pct is not None and drawdown_pct <= drawdown_thresh:
            reasons.append(f"52주 낙폭 {drawdown_pct}%")

        if trailing_pe is not None and trailing_pe < pe_threshold:
            reasons.append(f"PER {trailing_pe}배 (기준 {pe_threshold}배 미만)")
        elif forward_pe is not None and forward_pe < pe_threshold:
            reasons.append(f"예상PER {forward_pe}배 (기준 {pe_threshold}배 미만)")

        if dividend_yield is not None and dividend_yield >= max(div_req, dividend_thresh):
            reasons.append(f"배당수익률 {dividend_yield}%")

        if not reasons:
            print(f"  {name} ({ticker}): 기준 미달 → 스킵")
            continue

        # ── 역발상 여부 ─────────────────────────────────────────
        mention_count = trending_map.get(name, 0)
        contrarian    = mention_count < 2

        pick = {
            "ticker":          ticker,
            "name":            name,
            "sector":          meta["sector"],
            "current_price":   current_price,
            "drawdown_pct":    drawdown_pct,
            "trailing_pe":     trailing_pe,
            "forward_pe":      forward_pe,
            "dividend_yield":  dividend_yield,
            "value_reasons":   reasons,
            "contrarian":      contrarian,
            "mention_count":   mention_count,
            "fetch_status":    "ok",
        }
        picks.append(pick)

        tag = "⭐역발상" if contrarian else "📢화제"
        pe_str  = f"PER {trailing_pe}" if trailing_pe else ""
        div_str = f"배당 {dividend_yield}%" if dividend_yield else ""
        print(f"  ✅ {name} ({ticker}) [{tag}] {' | '.join(reasons)}")

    # 역발상(contrarian=True) 우선, 그 다음 낙폭 순
    picks.sort(key=lambda x: (not x["contrarian"], -(x["drawdown_pct"] or 0)))
    return picks


def run(force: bool = False) -> None:
    cache_days = _get_config().get("cache_days", 7)
    if not force and already_screened_recently():
        data      = json.loads(OUTPUT.read_text(encoding="utf-8"))
        last_date = data.get("date", "?")
        picks_cnt = data.get("picks_count", 0)
        print(f"[스킵] 최근 {cache_days}일 이내 스크리닝 결과 있음 ({last_date}, {picks_cnt}개). --force로 강제 실행 가능.")
        return

    print(f"[value_screener] {TODAY} 가치투자 스크리닝 시작\n")
    picks = screen()

    universe = _get_config()["universe"]
    MARKET_DIR.mkdir(exist_ok=True)
    output = {
        "date":          TODAY,
        "screened_count": len(universe),
        "picks_count":   len(picks),
        "picks":         picks,
        "note":          (
            "이 종목들은 PER·배당·낙폭 기준 가치 스크리닝 결과입니다. "
            "contrarian=true는 trending_stocks.json에 언급이 적어 시장에서 소외된 역발상 후보임을 의미합니다. "
            "참고용 예시이며 실제 투자 결정은 투자자 본인의 판단으로 이루어져야 합니다."
        ),
    }
    safe_write_json(OUTPUT, output)
    print(f"\n[완료] value_picks.json: {len(picks)}개 종목 저장")
    if picks:
        contrarian_cnt = sum(1 for p in picks if p["contrarian"])
        print(f"  역발상(contrarian): {contrarian_cnt}개 / 화제종목중저평가: {len(picks) - contrarian_cnt}개")


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(force=force)
