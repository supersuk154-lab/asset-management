"""
risk_calculator.py — 포트폴리오 정량 리스크 지표 연산 엔진
=============================================================

yfinance에서 수집한 일별 수정종가 수익률 데이터를 바탕으로
포트폴리오 가중 합성 수익률을 생성하고, 기관급 리스크 지표를 계산한다.

산출 지표:
  - 연환산 수익률 (Annualized Return)
  - 샤프 지수    (Sharpe Ratio)
  - 소르티노 비율 (Sortino Ratio)
  - 최대 낙폭    (MDD, Max Drawdown)
  - 칼마 비율    (Calmar Ratio)
  - 베타         (Beta vs 벤치마크)

역사적 스트레스 테스트 시나리오 (2008/2020/2022):
  - 포트폴리오 내 자산군 가중치를 적용해 각 위기 시점의 예상 하락률 추정

ETF 룩스루 (Look-through):
  - etf_holdings.json의 구성종목 비중과 포트폴리오 ETF 비중을 곱해
    실질 단일 종목 집중도를 계산하고 15% 초과 시 경고 트리거

외부 의존성:
  - numpy (선택): 설치 시 벡터 연산 사용, 미설치 시 순수 파이썬 폴백
  - yfinance: correlation_analyzer.py가 미리 캐시한 데이터 사용 (직접 호출 안함)
"""

import json
import math
from pathlib import Path
from typing import Optional

BASE_DIR   = Path(__file__).parent.parent
MARKET_DIR = BASE_DIR / "market_data"
ETF_HOLDINGS_FILE = MARKET_DIR / "etf_holdings.json"

# 무위험 수익률 (연간) — 한국 3년 국고채 기준 (동적 로드 실패 시 폴백 기본값)
RF_ANNUAL = 0.030  # 3.0%

# 동적 무위험 수익률 데이터 소스 (data_fetcher가 ECOS/FRED로 갱신)
MACRO_RAW_PATH = MARKET_DIR / "realtime_macro_raw.json"


def get_dynamic_risk_free_rate() -> float:
    """
    realtime_macro_raw.json에서 한국은행 기준금리를 읽어 연간 무위험 수익률로 반환.
    - kor_base_rate.value가 있으면: (기준금리/100) + 0.25%p(국고채 3년물 스프레드 가정)
    - 값이 없거나(API 키 미설정 등) 오류 시: RF_ANNUAL(3.0%) 폴백
    이를 통해 고금리/저금리 국면에서 Sharpe·Sortino 기준이 자동 보정된다.
    """
    if not MACRO_RAW_PATH.exists():
        return RF_ANNUAL
    try:
        data = json.loads(MACRO_RAW_PATH.read_text(encoding="utf-8"))
        kor_rate = (data.get("kor_base_rate") or {}).get("value")
        if kor_rate is not None:
            return (float(kor_rate) / 100.0) + 0.0025
    except Exception as e:
        print(f"[Warning] 동적 무위험 수익률 로드 실패: {e} → 기본값 {RF_ANNUAL * 100:.1f}% 적용")
    return RF_ANNUAL

# 역사적 위기 시나리오: 자산군별 최대 낙폭 (%)
STRESS_SCENARIOS = {
    "2008_금융위기": {
        "description": "2008 글로벌 금융위기 (서브프라임 사태)",
        "period": "2007-10 ~ 2009-03",
        "fx_krw_usd_change_pct": 40.0,   # 원/달러 +40% (1,000→1,400원) — 환노출 달러 자산 하락 완충
        "drawdowns": {
            "글로벌주식": -56.0,
            "한국주식":   -54.0,
            "미국주식":   -55.0,
            "신흥국주식": -66.0,
            "채권":        -5.0,
            "금":         -30.0,
            "현금":         0.0,
            "부동산":     -45.0,
        }
    },
    "2020_코로나": {
        "description": "2020 코로나19 팬데믹 폭락",
        "period": "2020-02 ~ 2020-03",
        "fx_krw_usd_change_pct": 10.0,   # 원/달러 +10% (1,180→1,300원)
        "drawdowns": {
            "글로벌주식": -34.0,
            "한국주식":   -36.0,
            "미국주식":   -34.0,
            "신흥국주식": -32.0,
            "채권":        -5.0,
            "금":         -12.0,
            "현금":         0.0,
            "부동산":     -20.0,
        }
    },
    "2022_금리인상": {
        "description": "2022 초고속 금리인상·인플레이션 충격",
        "period": "2022-01 ~ 2022-10",
        "fx_krw_usd_change_pct": 15.0,   # 원/달러 +15% (1,200→1,380원)
        "drawdowns": {
            "글로벌주식": -25.0,
            "한국주식":   -28.0,
            "미국주식":   -25.0,
            "신흥국주식": -30.0,
            "채권":       -18.0,  # 금리인상으로 채권도 타격
            "금":         -20.0,
            "현금":         0.0,
            "부동산":     -15.0,
        }
    }
}

# 자산군별 USD 비중 (FX 완충 계수) — 환노출 해외 자산 스트레스 시나리오에만 적용
_USD_EXPOSURE_COEF: dict[str, float] = {
    "미국주식":  1.0,   # 100% USD
    "글로벌주식": 0.6,  # MSCI World 기준 USD 비중 약 60%
}

# 자산 섹터 → 스트레스 자산군 매핑
SECTOR_TO_STRESS_ASSET = {
    # 국내 주식
    "IT/반도체":      "한국주식",
    "IT/플랫폼":      "한국주식",
    "바이오/제약":    "한국주식",
    "금융":           "한국주식",
    "금융/은행":      "한국주식",
    "에너지":         "한국주식",
    "에너지/배터리":  "한국주식",
    "헬스케어":       "한국주식",
    "소비재":         "한국주식",
    "자동차":         "한국주식",
    "자동차부품":     "한국주식",
    "배터리/2차전지": "한국주식",
    "화학/배터리":    "한국주식",
    "조선":           "한국주식",
    "방산/항공":      "한국주식",
    "철강/소재":      "한국주식",
    # 국내 ETF
    "국내/대형주ETF": "한국주식",
    # 미국 주식
    "미국/IT":        "미국주식",
    "미국/반도체":    "미국주식",
    "미국/전기차":    "미국주식",
    "미국/S&P500ETF": "미국주식",
    "미국/나스닥100ETF": "미국주식",
    # 채권·안전자산
    "채권":              "채권",
    "단기채":            "채권",
    "국내/단기채ETF":    "채권",
    "미국/국채ETF":      "채권",
    "혼합/채권혼합ETF":  "채권",
    "금/원자재":         "금",
    "금/원자재ETF":      "금",
    "현금":              "현금",
    "부동산":            "부동산",
    "글로벌ETF":         "글로벌주식",
}


# ─────────────────────────────────────
# numpy 안전 임포트 (선택 의존성)
# ─────────────────────────────────────
try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False


def _std(values: list) -> float:
    """표준편차 (순수 파이썬 폴백)"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _downside_std(excess_returns: list) -> float:
    """하방 편차 (0 미만 초과수익률만 사용)"""
    neg = [r for r in excess_returns if r < 0]
    if not neg:
        return 0.0
    mean_neg_sq = sum(r ** 2 for r in neg) / len(excess_returns)  # 전체 N으로 나눔
    return math.sqrt(mean_neg_sq) * math.sqrt(252)


# ─────────────────────────────────────
# 핵심 지표 연산 함수
# ─────────────────────────────────────
def calculate_advanced_metrics(
    portfolio_returns: list,
    benchmark_returns: Optional[list] = None,
    rf_annual: Optional[float] = None,
) -> dict:
    """
    포트폴리오 일별 수익률 시계열에서 정량 리스크 지표를 산출한다.

    Args:
        portfolio_returns: 포트폴리오 일별 수익률 리스트 (소수점, 예: 0.01 = 1%)
        benchmark_returns: 벤치마크(S&P500 또는 KOSPI200) 일별 수익률 (베타 계산용)
        rf_annual: 연간 무위험 수익률 (None이면 실시간 기준금리 연동, 폴백 3%)

    Returns:
        {sharpe_ratio, sortino_ratio, calmar_ratio, beta, mdd_pct, annualized_return_pct}
        데이터 부족(30일 미만) 시 빈 dict 반환
    """
    n = len(portfolio_returns)
    if n < 30:
        return {"note": f"데이터 부족 ({n}일, 최소 30일 필요) — 지표 산출 불가"}

    # 무위험 수익률: 명시값 없으면 실시간 기준금리 연동 (데이터 없으면 3.0% 폴백)
    if rf_annual is None:
        rf_annual = get_dynamic_risk_free_rate()

    rf_daily = (1 + rf_annual) ** (1 / 252) - 1

    if _NUMPY:
        rets = np.array(portfolio_returns[:n], dtype=float)
        excess = rets - rf_daily

        # 1. 연환산 수익률
        cum = float(np.prod(1 + rets)) - 1
        ann_ret = float((1 + cum) ** (252 / n) - 1)

        # 2. 샤프
        std_ex = float(np.std(excess))
        sharpe = float(np.mean(excess) / std_ex * math.sqrt(252)) if std_ex > 0 else 0.0

        # 3. 소르티노 (하방편차 = 0 기준 RMS — 순수 파이썬 _downside_std와 동일 정의)
        downside = np.minimum(excess, 0.0)
        d_std = float(np.sqrt(np.mean(downside ** 2)) * math.sqrt(252))
        sortino = float(np.mean(excess) * 252 / d_std) if d_std > 0 else 0.0

        # 4. MDD
        cum_prices = np.cumprod(1 + rets)
        running_max = np.maximum.accumulate(cum_prices)
        drawdowns = (cum_prices - running_max) / running_max
        mdd = float(np.min(drawdowns))

        # 5. 칼마
        calmar = ann_ret / abs(mdd) if mdd != 0 else 0.0

        # 6. 베타
        beta = 1.0
        if benchmark_returns and len(benchmark_returns) >= n:
            bench = np.array(benchmark_returns[:n], dtype=float)
            cov = float(np.cov(rets, bench)[0, 1])  # np.cov 기본 ddof=1 (N-1)
            var_m = float(np.var(bench, ddof=1))     # cov와 분모 통일 → 순수 파이썬 경로와 동일 베타
            beta = cov / var_m if var_m > 0 else 1.0

    else:
        # 순수 파이썬 폴백
        rets = portfolio_returns[:n]
        excess = [r - rf_daily for r in rets]

        # 1. 연환산 수익률
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        cum -= 1
        ann_ret = (1 + cum) ** (252 / n) - 1

        # 2. 샤프
        mean_ex = sum(excess) / n
        std_ex = _std(excess)
        sharpe = (mean_ex / std_ex * math.sqrt(252)) if std_ex > 0 else 0.0

        # 3. 소르티노
        d_std = _downside_std(excess)
        sortino = (mean_ex * 252 / d_std) if d_std > 0 else 0.0

        # 4. MDD
        cum_val = 1.0
        mdd = 0.0
        peak = 1.0
        for r in rets:
            cum_val *= (1 + r)
            if cum_val > peak:
                peak = cum_val
            dd = (cum_val - peak) / peak
            if dd < mdd:
                mdd = dd

        # 5. 칼마
        calmar = ann_ret / abs(mdd) if mdd != 0 else 0.0

        # 6. 베타 (간단 구현)
        beta = 1.0
        if benchmark_returns and len(benchmark_returns) >= n:
            bench = benchmark_returns[:n]
            mean_r = sum(rets) / n
            mean_b = sum(bench) / n
            cov = sum((rets[i] - mean_r) * (bench[i] - mean_b) for i in range(n)) / n
            var_m = sum((bench[i] - mean_b) ** 2 for i in range(n)) / n
            beta = cov / var_m if var_m > 0 else 1.0

    return {
        "annualized_return_pct": round(ann_ret * 100, 2),
        "sharpe_ratio":          round(sharpe, 2),
        "sortino_ratio":         round(sortino, 2),
        "mdd_pct":               round(mdd * 100, 2),
        "calmar_ratio":          round(calmar, 2),
        "beta":                  round(beta, 2),
        "data_days":             n,
        "rf_annual_used":        rf_annual,
        "note":                  "실제 보유 종목 수익률 기반 연산 (1년 백테스트)"
    }


# ─────────────────────────────────────
# 역사적 스트레스 테스트
# ─────────────────────────────────────
def run_stress_test(investments: list, total_invest: int) -> dict:
    """
    보유 종목 목록과 총 투자금액을 받아 3가지 역사적 위기 시나리오별
    예상 포트폴리오 손실을 추정한다.

    Args:
        investments: kyc.assets.investments 배열
        total_invest: kyc.assets.investments_total (원)

    Returns:
        {scenario_name: {drawdown_pct, estimated_loss_krw, description}}
    """
    if not investments or total_invest <= 0:
        return {}

    # 종목별 비중 계산
    def get_sector_class(inv: dict) -> str:
        sector = inv.get("sector", "")
        market = inv.get("market", "KR")
        if not sector:
            return "글로벌주식" if market.startswith("US") else "한국주식"
        return SECTOR_TO_STRESS_ASSET.get(
            sector,
            "한국주식" if market.startswith("KR") else "미국주식"
        )

    # 종목별 (자산군, 비중, 환헤지 여부) — FX 완충 계산을 위해 개별 보존
    inv_info: list[tuple[str, float, bool]] = []
    for inv in investments:
        amount = inv.get("amount", 0)
        if amount <= 0:
            continue
        asset_class = get_sector_class(inv)
        weight = amount / total_invest
        # fx_hedged: None → 환노출(False) 처리 (달러 자산 기본값)
        fx_hedged = bool(inv.get("fx_hedged") or False)
        inv_info.append((asset_class, weight, fx_hedged))

    results = {}
    for scenario_name, scenario in STRESS_SCENARIOS.items():
        port_drawdown = 0.0
        fx_change = scenario.get("fx_krw_usd_change_pct", 0.0)

        for asset_class, weight, fx_hedged in inv_info:
            base_dd = scenario["drawdowns"].get(asset_class, -30.0)

            # FX 환쿠션: 환노출 달러 자산은 원/달러 급등이 주가 하락 일부를 상쇄
            usd_coef = _USD_EXPOSURE_COEF.get(asset_class, 0.0)
            if not fx_hedged and fx_change > 0 and usd_coef > 0:
                # (1 + 주가낙폭) × (1 + 환율상승 × USD비중) - 1
                adjusted_dd = (
                    (1 + base_dd / 100) * (1 + fx_change / 100 * usd_coef) - 1
                ) * 100
                port_drawdown += weight * round(adjusted_dd, 2)
            else:
                port_drawdown += weight * base_dd

        estimated_loss = int(total_invest * port_drawdown / 100)
        results[scenario_name] = {
            "description":            scenario["description"],
            "period":                 scenario["period"],
            "portfolio_drawdown_pct": round(port_drawdown, 1),
            "estimated_loss_krw":     estimated_loss,
        }

    return results


# ─────────────────────────────────────
# ETF 룩스루 (Look-through) 분석
# ─────────────────────────────────────
def run_etf_lookthrough(investments: list, total_invest: int) -> dict:
    """
    보유 ETF의 내부 구성종목 비중을 가중 합산하여
    실질 단일 종목 집중도를 계산한다.

    Args:
        investments: kyc.assets.investments 배열 (ticker 포함)
        total_invest: kyc.assets.investments_total (원)

    Returns:
        {
          "total_exposure": {종목명: 실질비중(%)},
          "concentration_warnings": [{company, exposure_pct, warning}],
          "lookthrough_available": bool
        }
    """
    if not ETF_HOLDINGS_FILE.exists():
        return {"lookthrough_available": False, "note": "etf_holdings.json 없음"}

    try:
        etf_db = json.loads(ETF_HOLDINGS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"lookthrough_available": False, "note": f"etf_holdings.json 로드 실패: {e}"}

    if not investments or total_invest <= 0:
        return {"lookthrough_available": False, "note": "투자 종목 없음"}

    company_exposure: dict[str, float] = {}

    # Step 1: 개별 종목 직접 보유분 — ETF 룩스루 이전에 먼저 합산
    # (삼성전자 직접 보유 + KODEX200 내 삼성전자 모두 반영해야 진짜 집중도가 나옴)
    for inv in investments:
        ticker = inv.get("ticker")
        amount = inv.get("amount", 0)
        if not ticker or amount <= 0:
            continue
        if ticker not in etf_db:  # ETF가 아닌 개별 주식/채권
            name = inv.get("standard_name") or inv.get("name", ticker)
            if name:
                direct_pct = (amount / total_invest) * 100
                company_exposure[name] = company_exposure.get(name, 0) + direct_pct

    # Step 2: ETF 룩스루 — 구성 종목 비중 가중 합산
    for inv in investments:
        ticker = inv.get("ticker")
        if not ticker:
            continue

        amount = inv.get("amount", 0)
        if amount <= 0:
            continue

        port_weight = amount / total_invest

        etf_info = etf_db.get(ticker)
        if not etf_info:
            continue

        holdings = etf_info.get("top_holdings", [])
        for holding in holdings:
            company = holding.get("name")
            etf_weight = holding.get("weight_pct", 0) / 100
            if company:
                exposure = port_weight * etf_weight * 100
                company_exposure[company] = company_exposure.get(company, 0) + exposure

    # 15% 초과 종목 경고
    CONCENTRATION_THRESHOLD = 15.0
    warnings = []
    for company, exposure in sorted(company_exposure.items(), key=lambda x: -x[1]):
        if exposure >= CONCENTRATION_THRESHOLD:
            warnings.append({
                "company":      company,
                "exposure_pct": round(exposure, 1),
                "warning":      f"ETF 룩스루 결과 {company}의 실질 비중이 {exposure:.1f}%로 임계치({CONCENTRATION_THRESHOLD}%)를 초과합니다. 특정 기업 집중 리스크를 확인하세요."
            })

    return {
        "lookthrough_available": True,
        "total_exposure": {k: round(v, 2) for k, v in
                          sorted(company_exposure.items(), key=lambda x: -x[1])[:10]},
        "concentration_warnings": warnings,
        "threshold_pct": CONCENTRATION_THRESHOLD,
    }


# ─────────────────────────────────────
# 글라이드 패스 목표 비중 계산
# ─────────────────────────────────────
def calc_glide_path_target(age: int, goal_type: str = "retirement",
                           job_type: str = "급여소득자",
                           goal_years_remaining: Optional[int] = None) -> dict:
    """
    TDF 스타일 글라이드 패스 공식으로 연령·목표·직업형·목표기간 기반 위험자산 비중을 계산.

    공식:
      청년기 (age <= 35):  risky = 90% (인적 자본 풍부, 최대 위험 허용)
      장년기 (35 < age <= 60): risky = max(30, 90 - 1.6 × (age - 35))
      은퇴기 (age > 60):  risky = 30% (연금 인출기 보호, 하한선)

    단기 목표(housing, short_term) 보정:
      goal_years_remaining 제공 시 → 목표 기간별 Hard Cap 우선 적용
        ≤ 3년: 위험자산 최대 20% (단기 자금 손실 방지)
        4~7년: 위험자산 최대 40%
        8년+: 기존 × 0.6 배율 적용
      goal_years_remaining 없을 시 → 기존 × 0.6 배율 적용

    직업형 인적자본 보정 (job_type):
      자영업/프리랜서: 소득이 경기와 연동(주식형 인적자본) → 금융자산 -10%p 보수화
      급여소득자: 안정적 채권형 소득 → 보정 없음

    Returns:
        {target_risky_pct, target_safe_pct, formula_used, tolerance_band,
         job_type_adjustment, hard_cap_applied, hard_cap_value}
    """
    if age <= 35:
        base_risky = 90.0
        formula = "청년기 고정 90% (인적 자본 풍부)"
    elif age <= 60:
        base_risky = max(30.0, 90.0 - 1.6 * (age - 35))
        formula = f"장년기 선형 감축: max(30, 90 - 1.6×({age}-35)) = {base_risky:.1f}%"
    else:
        base_risky = 30.0
        formula = "은퇴기 하한 30% 고정 (연금 인출기 보호)"

    # 단기 목표 보정 — goal_years_remaining 기반 Hard Cap 우선 적용
    goal_multiplier = 1.0
    hard_cap_applied = False
    hard_cap_value: Optional[float] = None

    if goal_type in ("housing", "short_term"):
        if goal_years_remaining is not None:
            if goal_years_remaining <= 3:
                hard_cap_value = 20.0
                formula += f" | 목표 {goal_years_remaining}년 이내 하드캡 → 위험자산 최대 20%"
            elif goal_years_remaining <= 7:
                hard_cap_value = 40.0
                formula += f" | 목표 {goal_years_remaining}년 이내 하드캡 → 위험자산 최대 40%"
            else:
                # 8년 이상 — 기존 0.6 배율 적용
                base_risky = base_risky * 0.6
                goal_multiplier = 0.6
                formula += f" × 단기목표 보정 0.6 = {base_risky:.1f}%"
        else:
            # goal_years_remaining 미제공 시 기존 0.6 배율
            base_risky = base_risky * 0.6
            goal_multiplier = 0.6
            formula += f" × 단기목표 보정 0.6 = {base_risky:.1f}%"

    # 직업형 인적자본 보정 (단기 목표는 이미 보수화되므로 적용 제외)
    job_type_adjustment = 0
    if job_type == "자영업/프리랜서" and goal_type not in ("housing", "short_term"):
        job_type_adjustment = -10
        base_risky = max(20.0, base_risky - 10.0)
        formula += f" | 자영업/프리랜서 인적자본 보정 -10%p → {base_risky:.1f}%"

    # 클램핑 (최소 20%, 최대 90%)
    target_risky = max(20.0, min(90.0, base_risky))
    target_safe  = 100.0 - target_risky

    # 허용 이탈 밴드 (Tolerance ±10%p — 이 범위 벗어나면 경고)
    TOLERANCE = 10.0

    # Hard Cap 후처리 (클램핑 이후 최종 적용)
    if hard_cap_value is not None and target_risky > hard_cap_value:
        target_risky = hard_cap_value
        target_safe  = 100.0 - target_risky
        hard_cap_applied = True

    return {
        "target_risky_pct":    round(target_risky, 1),
        "target_safe_pct":     round(target_safe, 1),
        "formula_used":        formula,
        "tolerance_band_pct":  TOLERANCE,
        "upper_limit":         min(90.0, round(target_risky + TOLERANCE, 1)),
        "lower_limit":         max(20.0, round(target_risky - TOLERANCE, 1)),
        "job_type_adjustment": job_type_adjustment,
        "hard_cap_applied":    hard_cap_applied,
        "hard_cap_value":      hard_cap_value,
    }
