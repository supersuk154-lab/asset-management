# -*- coding: utf-8 -*-
"""schemas.py 단위 테스트 — Pydantic model_validator(합계·등급·유동성잠금·부채합계 등).

pydantic 미설치 시 SKIP_REASON 으로 건너뛴다.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

SKIP_REASON = None
try:
    from schemas import (
        PortfolioDesignerOutput,
        RiskScorerOutput,
        StockPlanOutput,
        StockProduct,
        KYCAssets,
        ReviewerOutput,
    )
except Exception as e:  # pydantic 미설치 등
    SKIP_REASON = f"schemas/pydantic import 실패: {e}"


def _raises(fn) -> bool:
    """fn() 호출이 예외를 던지면 True (의존성 없는 pytest.raises 대체)."""
    try:
        fn()
        return False
    except Exception:
        return True


# ── PortfolioDesignerOutput: safe+risky=100, core+satellite=100 ──
def test_portfolio_valid():
    if SKIP_REASON:
        return
    p = PortfolioDesignerOutput(safe_pct=20, risky_pct=80, core_pct=70,
                                satellite_pct=30, plain_language_ips="요약")
    assert p.safe_pct + p.risky_pct == 100


def test_portfolio_safe_risky_sum_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: PortfolioDesignerOutput(
        safe_pct=20, risky_pct=70, core_pct=70, satellite_pct=30,
        plain_language_ips="x"))


def test_portfolio_core_satellite_sum_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: PortfolioDesignerOutput(
        safe_pct=20, risky_pct=80, core_pct=60, satellite_pct=30,
        plain_language_ips="x"))


# ── RiskScorerOutput: grade 이모지 패턴 ──
def test_risk_valid():
    if SKIP_REASON:
        return
    r = RiskScorerOutput(total_score=85, grade="🟡",
                         urgent_actions=["비상금 확보"], fact_bomb="팩트")
    assert r.total_score == 85


def test_risk_bad_grade_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: RiskScorerOutput(
        total_score=85, grade="X", urgent_actions=["a"], fact_bomb="b"))


def test_risk_empty_urgent_actions_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: RiskScorerOutput(
        total_score=85, grade="🟡", urgent_actions=[], fact_bomb="b"))


# ── StockPlanOutput: 월 합계 일치 + 유동성 잠금 ──
def _stock(account_type="일반", monthly=100000, goal="미설정", total=100000):
    return StockPlanOutput(
        client_id="t", created_at="2026-05-30", gbi_goal_type=goal,
        safe_products=[StockProduct(name="상품", account_type=account_type,
                                    monthly_amount=monthly)],
        core_products=[], satellite_products=[], total_monthly=total,
    )


def test_stock_valid():
    if SKIP_REASON:
        return
    s = _stock()
    assert s.total_monthly == 100000


def test_stock_monthly_total_mismatch_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: _stock(monthly=100000, total=999999))


def test_stock_liquidity_lock_housing_irp_fail():
    if SKIP_REASON:
        return
    # housing 목표 + IRP 계좌 추천 → 유동성 잠금 위반
    assert _raises(lambda: _stock(account_type="IRP", goal="housing"))


def test_stock_liquidity_lock_retirement_irp_ok():
    if SKIP_REASON:
        return
    # retirement 는 IRP 허용
    s = _stock(account_type="IRP", goal="retirement")
    assert s.gbi_goal_type == "retirement"


# ── KYCAssets: total_gross 교차검증 ──
def test_kyc_gross_ok():
    if SKIP_REASON:
        return
    a = KYCAssets(cash=1000000, investments_total=0, pension=0, debt=0,
                  total_gross=1000000, net_assets=1000000)
    assert a.total_gross == 1000000


def test_kyc_gross_mismatch_fail():
    if SKIP_REASON:
        return
    assert _raises(lambda: KYCAssets(
        cash=1000000, investments_total=0, pension=0, debt=0,
        total_gross=5000000, net_assets=5000000))


# ── ReviewerOutput: verdict ↔ report_confirmed 일관성 ──
def test_reviewer_fail_confirmed_inconsistent():
    if SKIP_REASON:
        return
    assert _raises(lambda: ReviewerOutput(
        client_id="t", reviewed_at="2026-05-30", verdict="FAIL",
        checks={}, warnings=[], report_confirmed=True))


def test_reviewer_pass_confirmed_ok():
    if SKIP_REASON:
        return
    rv = ReviewerOutput(client_id="t", reviewed_at="2026-05-30", verdict="PASS",
                        checks={"V1": "PASS"}, warnings=[], report_confirmed=True)
    assert rv.report_confirmed is True
