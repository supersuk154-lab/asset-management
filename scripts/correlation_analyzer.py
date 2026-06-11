"""
correlation_analyzer.py — 보유 자산 상관계수 분석 및 가짜 분산 감지
=====================================================================

사용법:
    python scripts/correlation_analyzer.py <client_id>

동작:
1. kyc.json의 assets.investments 에서 ticker 목록 추출
2. yfinance로 최근 1년 일별 수정 종가 수집 (7일 캐시 활용)
3. 피어슨 상관계수 행렬 계산
4. r >= 0.8 쌍 감지 → pseudo_diversification_detected 플래그
5. 포트폴리오 분산 점수 (0~100) 산출
6. [신규] 포트폴리오 가중 수익률로 Sharpe/Sortino/MDD/Calmar/Beta 연산 (risk_calculator 연동)
7. [신규] 역사적 스트레스 테스트 (2008/2020/2022 시나리오)
8. [신규] ETF 룩스루 중복 분석 (etf_holdings.json 연동)
9. data/clients/{id}/correlation_analysis.json 저장

yfinance 실패 시: 섹터 기반 정적 행렬로 fallback (r=0.7)
"""

from __future__ import annotations

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from itertools import combinations

# risk_calculator 연동 (같은 scripts/ 디렉토리)
_scripts_dir = Path(__file__).parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
try:
    from risk_calculator import (
        calculate_advanced_metrics,
        run_stress_test,
        run_etf_lookthrough,
    )
    RISK_CALC_AVAILABLE = True
except ImportError as e:
    print(f"  [경고] risk_calculator 임포트 실패: {e} → 정량 지표 산출 스킵")
    RISK_CALC_AVAILABLE = False

from utils import safe_write_json
import price_cache as _pc

# ─────────────────────────────────────
# Windows 콘솔 인코딩
# ─────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "data"
MARKET_DIR  = BASE_DIR / "market_data"

TODAY = datetime.now().strftime("%Y-%m-%d")

# ─────────────────────────────────────
# 자산군 기반 정적 상관계수 (fallback)
# yfinance 실패/티커 없음 시, 섹터를 국가·자산군(class)으로 매핑해
# 국가 간 분산효과 및 안전자산(채권·금·달러)과의 음의 상관까지 반영한다.
# ─────────────────────────────────────

# 1) 섹터 문자열 → 자산군(class) 매핑
ASSET_CLASS_MAP = {
    # 국내 주식
    "IT/반도체": "KOR_EQ", "IT/플랫폼": "KOR_EQ",
    "바이오/제약": "KOR_EQ", "금융": "KOR_EQ", "금융/은행": "KOR_EQ",
    "에너지": "KOR_EQ", "에너지/배터리": "KOR_EQ",
    "소비재": "KOR_EQ", "자동차": "KOR_EQ", "자동차부품": "KOR_EQ",
    "배터리/2차전지": "KOR_EQ", "화학/배터리": "KOR_EQ",
    "헬스케어": "KOR_EQ", "조선": "KOR_EQ", "방산/항공": "KOR_EQ",
    "철강/소재": "KOR_EQ", "국내/대형주ETF": "KOR_EQ",
    # 미국·글로벌 주식
    "미국/IT": "US_EQ", "미국/반도체": "US_EQ", "미국/전기차": "US_EQ",
    "미국/S&P500ETF": "US_EQ", "미국/나스닥100ETF": "US_EQ", "글로벌ETF": "US_EQ",
    # 채권
    "채권": "KOR_BOND", "단기채": "KOR_BOND", "국내/단기채ETF": "KOR_BOND",
    "미국/국채ETF": "US_BOND",
    # 대안·안전자산
    "금/원자재": "GOLD", "금/원자재ETF": "GOLD",
    "현금": "CMA",
}

# 2) 자산군 간 정적 상관계수 (조회 시 순서 무관 — 양방향 매칭)
STATIC_CLASS_CORRELATION = {
    ("KOR_EQ", "KOR_EQ"): 0.85,       # 높은 동조화 (가짜 분산 경고 대상)
    ("US_EQ", "US_EQ"): 0.80,
    ("KOR_EQ", "US_EQ"): 0.50,        # 국가 간 분산 효과
    ("KOR_EQ", "KOR_BOND"): -0.05,    # 주식↔채권 약한 음의 상관
    ("US_EQ", "US_BOND"): -0.15,
    ("KOR_EQ", "US_BOND"): -0.25,     # 달러 강세 효과로 강한 음의 상관 (환헤지 안전판)
    ("KOR_EQ", "GOLD"): 0.10,         # 금 인플레 헤지 대안자산
    ("US_EQ", "GOLD"): 0.15,
    ("KOR_BOND", "US_BOND"): 0.35,
    ("KOR_BOND", "GOLD"): 0.05,
    ("US_BOND", "GOLD"): 0.10,
    ("CMA", "KOR_BOND"): 0.20,
}

# (하위 호환) 기존 상수명 별칭 — 혹시 모를 외부 참조 대비
STATIC_SECTOR_CORRELATION = STATIC_CLASS_CORRELATION


def _asset_class(sector: str) -> str:
    """섹터 문자열을 자산군(class)으로 변환. 미매핑 시 국내주식(KOR_EQ) 기본."""
    if not sector:
        return "KOR_EQ"
    return ASSET_CLASS_MAP.get(sector, "KOR_EQ")


def get_static_correlation(sector_a: str, sector_b: str) -> float:
    """
    확장된 자산군 분류 기반 정적 상관계수 Fallback.
    섹터를 국가·자산군(class)으로 매핑 후 상관계수를 양방향 조회한다.
    동일 자산군 미정의 시 0.70, 교차 미정의 시 0.20 기본값.
    """
    class_a = _asset_class(sector_a)
    class_b = _asset_class(sector_b)
    if class_a == class_b:
        return STATIC_CLASS_CORRELATION.get((class_a, class_a), 0.70)
    if (class_a, class_b) in STATIC_CLASS_CORRELATION:
        return STATIC_CLASS_CORRELATION[(class_a, class_b)]
    if (class_b, class_a) in STATIC_CLASS_CORRELATION:
        return STATIC_CLASS_CORRELATION[(class_b, class_a)]
    return 0.20


# ─────────────────────────────────────
# 주가 데이터 수집 (price_cache 경유)
# ─────────────────────────────────────
def fetch_price_data(tickers: list) -> tuple[dict, list, bool]:
    """
    Returns: (prices_dict, failed_tickers, yfinance_available)
    prices_dict: {ticker: [daily_return, ...]}
    price_cache.ensure_cached 경유 — 7일 TTL 내 캐시 적중 시 yfinance 호출 생략.
    """
    try:
        import yfinance  # noqa: F401 — yfinance 설치 여부 확인용
        yfinance_ok = True
    except ImportError:
        print("  [경고] yfinance 미설치 → 섹터 기반 정적 fallback 사용")
        yfinance_ok = False

    cache = _pc.ensure_cached(tickers)

    prices: dict[str, list] = {}
    failed: list[str] = []

    for ticker in tickers:
        close_dict = _pc.get_close_series(ticker, cache)
        if not close_dict or len(close_dict) < 30:
            print(f"  [실패] {ticker}: 캐시 데이터 없음 또는 부족")
            failed.append(ticker)
            continue

        closes  = list(close_dict.values())
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] and closes[i - 1] != 0:
                returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if len(returns) < 29:
            failed.append(ticker)
            continue

        prices[ticker] = returns
        status = "캐시" if not yfinance_ok else "price_cache"
        print(f"  [{status}] {ticker} — {len(returns)}일 수익률")

    return prices, failed, yfinance_ok


# ─────────────────────────────────────
# 피어슨 상관계수 계산
# ─────────────────────────────────────
def pearson_correlation(x: list, y: list) -> float:
    """두 수익률 시리즈의 피어슨 상관계수 계산 (pandas 없이 순수 파이썬)."""
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    x, y = x[:n], y[:n]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
    den_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return round(num / (den_x * den_y), 4)


# ─────────────────────────────────────
# 분산도 점수 계산 (0~100)
# ─────────────────────────────────────
def calc_diversification_score(pairs: list) -> int:
    """
    쌍별 상관계수를 평균 → 분산도 점수로 환산.
    평균 r=0.0 → 100점, 평균 r=1.0 → 0점.
    종목 1개 이하 → 50점 (중립).
    """
    if not pairs:
        return 50
    avg_r = sum(abs(p["correlation"]) for p in pairs) / len(pairs)
    score = max(0, min(100, round((1 - avg_r) * 100)))
    return score


# ─────────────────────────────────────
# 상관 쌍 verdict 문구
# ─────────────────────────────────────
def verdict_text(r: float) -> str:
    if r >= 0.90:
        return "매우 높은 상관관계 (사실상 중복 투자 — Core 교체 권고)"
    elif r >= 0.80:
        return "높은 상관관계 (가짜 분산 경고 — 비중 조정 권고)"
    elif r >= 0.60:
        return "중간 상관관계 (주의 수준)"
    else:
        return "낮은 상관관계 (정상 분산)"


# ─────────────────────────────────────
# 정량 지표 연산 헬퍼
# ─────────────────────────────────────
def _build_portfolio_returns(valid_invs: list, prices: dict, all_invs: list) -> list:
    """
    보유 종목별 금액 가중치를 적용해 포트폴리오 합성 일별 수익률을 생성한다.
    가격 데이터가 없는 종목은 시장 평균(0.0)으로 처리한다.
    """
    total_amount = sum(inv.get("amount", 0) for inv in valid_invs if inv.get("ticker") in prices)
    if total_amount <= 0:
        return []

    # 최소 공통 길이 결정
    available = {inv["ticker"]: prices[inv["ticker"]]
                 for inv in valid_invs if inv.get("ticker") in prices}
    if not available:
        return []

    min_len = min(len(r) for r in available.values())
    if min_len < 30:
        return []

    port_returns = [0.0] * min_len
    for inv in valid_invs:
        ticker = inv.get("ticker")
        if ticker not in prices:
            continue
        amount = inv.get("amount", 0)
        weight = amount / total_amount
        rets = prices[ticker][:min_len]
        for i, r in enumerate(rets):
            port_returns[i] += weight * r

    return port_returns


def _get_benchmark_returns() -> list:
    """
    price_cache에서 SPY(미국) 또는 069500.KS(한국) 수익률을 벤치마크로 반환.
    없으면 빈 리스트.
    """
    bench_tickers = ["SPY", "069500.KS", "^GSPC"]
    cache = _pc.ensure_cached(bench_tickers)
    for bench_ticker in bench_tickers:
        close_dict = _pc.get_close_series(bench_ticker, cache)
        if close_dict and len(close_dict) >= 30:
            closes = list(close_dict.values())
            returns = []
            for i in range(1, len(closes)):
                if closes[i - 1] and closes[i - 1] != 0:
                    returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
            if len(returns) >= 29:
                return returns
    return []


# ─────────────────────────────────────
# 메인 분석 로직
# ─────────────────────────────────────
def analyze(client_id: str):
    client_dir = DATA_DIR / "clients" / client_id
    kyc_path   = client_dir / "kyc.json"
    out_path   = client_dir / "correlation_analysis.json"

    if not kyc_path.exists():
        print(f"[ERROR] kyc.json 없음: {kyc_path}")
        sys.exit(1)

    kyc = json.loads(kyc_path.read_text(encoding="utf-8"))
    investments = kyc.get("assets", {}).get("investments", [])

    # ticker가 있는 종목만 추출
    valid = [
        inv for inv in investments
        if inv.get("ticker") and not inv.get("needs_review")
    ]

    print(f"[CORRELATION] {client_id} — 분석 대상 {len(valid)}개 종목")

    # 종목 1개 이하 → 분석 불필요
    if len(valid) < 2:
        result = {
            "client_id": client_id,
            "analyzed_at": TODAY,
            "portfolio_diversification_score": 50,
            "pseudo_diversification_detected": False,
            "high_correlation_pairs": [],
            "action_nudge": None,
            "fallback_used": False,
            "note": f"분석 가능 종목 {len(valid)}개 (최소 2개 필요) — 중립 점수(50점) 적용"
        }
        safe_write_json(out_path, result)
        print(f"  ✅ 저장 완료 (종목 부족 → 중립): {out_path}")
        return

    tickers = [inv["ticker"] for inv in valid]
    ticker_to_inv = {inv["ticker"]: inv for inv in valid}

    # 주가 데이터 수집 (price_cache 경유)
    prices, failed_tickers, yfinance_ok = fetch_price_data(tickers)

    # 쌍별 상관계수 계산
    all_pairs = []
    fallback_used = False

    for t_a, t_b in combinations(tickers, 2):
        inv_a = ticker_to_inv[t_a]
        inv_b = ticker_to_inv[t_b]
        name_a = inv_a.get("standard_name") or inv_a.get("name") or t_a
        name_b = inv_b.get("standard_name") or inv_b.get("name") or t_b

        if t_a in prices and t_b in prices:
            r = pearson_correlation(prices[t_a], prices[t_b])
        else:
            # fallback: 섹터 기반 정적 상관계수
            sector_a = inv_a.get("sector", "기타")
            sector_b = inv_b.get("sector", "기타")
            r = get_static_correlation(sector_a, sector_b)
            fallback_used = True
            print(f"  [fallback] {name_a} ↔ {name_b}: sector 기반 r={r}")

        all_pairs.append({
            "asset_a": name_a,
            "asset_b": name_b,
            "correlation": r,
            "verdict": verdict_text(r),
        })

    # r >= 0.8 쌍 필터
    high_pairs = [p for p in all_pairs if p["correlation"] >= 0.80]
    pseudo_detected = len(high_pairs) > 0

    # 분산도 점수
    div_score = calc_diversification_score(all_pairs)

    # 처방 넛지 문구
    if pseudo_detected:
        worst = max(high_pairs, key=lambda p: p["correlation"])
        action_nudge = (
            f"⚠️ {worst['asset_a']}과(와) {worst['asset_b']}의 상관계수가 "
            f"{worst['correlation']:.2f}로 매우 높습니다. "
            "사실상 같은 방향으로 움직이는 자산을 중복 보유하고 있어 "
            "실질적인 분산 효과가 없습니다. "
            "비우량 종목을 반등 시 분할 매도하고 상관관계가 낮은 "
            "채권 ETF나 금 등으로 교체하는 것을 권고합니다."
        )
    else:
        action_nudge = None

    note_parts = []
    if fallback_used:
        note_parts.append(f"yfinance 미수집 종목 {len(failed_tickers)}개 → 섹터 기반 정적 상관계수 적용")
    if not yfinance_ok:
        note_parts.append("yfinance 미설치 — 전체 섹터 fallback 사용")

    # ─────────────────────────────────────────────────────
    # [신규] 정량 리스크 지표 연산 (risk_calculator 연동)
    # ─────────────────────────────────────────────────────
    portfolio_metrics = {}
    stress_test_result = {}
    etf_lookthrough_result = {}

    if RISK_CALC_AVAILABLE:
        total_invest = kyc.get("assets", {}).get("investments_total", 0)

        # 1. 포트폴리오 가중 합성 수익률 생성
        portfolio_returns = _build_portfolio_returns(valid, prices, investments)

        # 2. 벤치마크 수익률 (price_cache에서 직접 로드)
        benchmark_returns = _get_benchmark_returns()

        # 3. Sharpe / Sortino / MDD / Calmar / Beta 연산
        if portfolio_returns:
            print("  [정량지표] 포트폴리오 가중 수익률 기반 Sharpe/MDD/Calmar 연산 중...")
            portfolio_metrics = calculate_advanced_metrics(
                portfolio_returns, benchmark_returns
            )
            sr = portfolio_metrics.get("sharpe_ratio", "N/A")
            mdd = portfolio_metrics.get("mdd_pct", "N/A")
            print(f"  │ Sharpe: {sr} | MDD: {mdd}% | Calmar: {portfolio_metrics.get('calmar_ratio', 'N/A')}")
        else:
            portfolio_metrics = {"note": "가격 데이터 미수집 — 지표 연산 불가 (fallback 사용 중)"}

        # 4. 역사적 스트레스 테스트
        if total_invest > 0:
            print("  [스트레스테스트] 2008/2020/2022 시나리오 시뮬레이션 중...")
            stress_test_result = run_stress_test(investments, total_invest)
            for scen, res in stress_test_result.items():
                loss_m = abs(res.get("estimated_loss_krw", 0)) // 10000
                print(f"  │ {scen}: {res.get('portfolio_drawdown_pct', 0):.1f}% 하락 → 약 {loss_m}만원 손실 추정")

        # 5. ETF 룩스루 분석
        print("  [ETF룩스루] 구성종목 중복 노출도 분석 중...")
        etf_lookthrough_result = run_etf_lookthrough(investments, total_invest)
        warnings = etf_lookthrough_result.get("concentration_warnings", [])
        if warnings:
            for w in warnings:
                print(f"  │ ⚠️ 집중 경고: {w['company']} {w['exposure_pct']}%")
        elif etf_lookthrough_result.get("lookthrough_available"):
            print("  │ ✅ ETF 룩스루 집중 경고 없음")

    result = {
        "client_id": client_id,
        "analyzed_at": TODAY,
        "portfolio_diversification_score": div_score,
        "pseudo_diversification_detected": pseudo_detected,
        "high_correlation_pairs": high_pairs,
        "action_nudge": action_nudge,
        "fallback_used": fallback_used,
        "note": " | ".join(note_parts) if note_parts else None,
        # 신규 필드
        "portfolio_metrics":    portfolio_metrics if portfolio_metrics else None,
        "stress_test":          stress_test_result if stress_test_result else None,
        "etf_lookthrough":      etf_lookthrough_result if etf_lookthrough_result else None,
    }

    safe_write_json(out_path, result)

    print(f"\n  분산도 점수: {div_score}점")
    print(f"  가짜 분산 감지: {'⚠️ YES' if pseudo_detected else '✅ NO'}")
    if high_pairs:
        for p in high_pairs:
            print(f"  [{p['correlation']:.2f}] {p['asset_a']} ↔ {p['asset_b']}: {p['verdict']}")
    print(f"  ✅ 저장 완료: {out_path}")


# ─────────────────────────────────────
# 진입점
# ─────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python scripts/correlation_analyzer.py <client_id>")
        sys.exit(1)
    analyze(sys.argv[1])
