# -*- coding: utf-8 -*-
"""risk_calculator.py 단위 테스트 — 글라이드 패스 공식, 동적 무위험수익률, 정량지표."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import risk_calculator as rc

SKIP_REASON = None


# ── 글라이드 패스 (TDF 연령 굴절형 — 2026-05-30 수정: 35세 이하 일률 90%) ──
def test_glide_path_youth_is_90_not_85():
    # 35세는 90%여야 한다 (단순 120-35=85 가 아님)
    assert rc.calc_glide_path_target(25)["target_risky_pct"] == 90.0
    assert rc.calc_glide_path_target(35)["target_risky_pct"] == 90.0


def test_glide_path_midlife_linear():
    assert rc.calc_glide_path_target(45)["target_risky_pct"] == 74.0   # 90-1.6*10
    assert rc.calc_glide_path_target(50)["target_risky_pct"] == 66.0   # 90-1.6*15
    assert rc.calc_glide_path_target(55)["target_risky_pct"] == 58.0   # 90-1.6*20
    assert rc.calc_glide_path_target(60)["target_risky_pct"] == 50.0   # 90-1.6*25


def test_glide_path_retirement_floor():
    assert rc.calc_glide_path_target(65)["target_risky_pct"] == 30.0
    assert rc.calc_glide_path_target(75)["target_risky_pct"] == 30.0


def test_glide_path_housing_multiplier():
    # housing/short_term 은 ×0.6 보정
    assert rc.calc_glide_path_target(45, "housing")["target_risky_pct"] == 44.4   # 74*0.6
    assert rc.calc_glide_path_target(55, "short_term")["target_risky_pct"] == 34.8  # 58*0.6


def test_glide_path_band_and_sum():
    gp = rc.calc_glide_path_target(55)
    assert gp["target_safe_pct"] == 42.0          # 100 - 58
    assert gp["lower_limit"] == 48.0              # 58 - 10
    assert gp["upper_limit"] == 68.0              # 58 + 10
    # safe + risky == 100
    assert abs(gp["target_risky_pct"] + gp["target_safe_pct"] - 100.0) < 1e-9


def test_glide_path_upper_clamp_90():
    # 청년 90% + 밴드 상한은 90 으로 클램프
    assert rc.calc_glide_path_target(25)["upper_limit"] == 90.0


# ── 동적 무위험 수익률 (2026-05-30 신규) ──
def test_dynamic_rf_fallback_when_null():
    # kor_base_rate.value 가 null(API 키 없음) → 3.0% 폴백
    rf = rc.get_dynamic_risk_free_rate()
    assert 0.0 < rf < 0.20          # 합리적 범위
    # 현재 환경(키 없음)에서는 정확히 폴백값
    assert abs(rf - rc.RF_ANNUAL) < 1e-9


# ── 정량 지표 ──
def test_advanced_metrics_insufficient_data():
    out = rc.calculate_advanced_metrics([0.01, -0.01])   # 30일 미만
    assert "note" in out and "부족" in out["note"]


def test_advanced_metrics_basic_and_dynamic_rf():
    rets = [0.001 * ((-1) ** i) + 0.0003 for i in range(150)]
    m = rc.calculate_advanced_metrics(rets)               # rf 미전달 → 동적 연동
    for k in ("sharpe_ratio", "sortino_ratio", "mdd_pct", "calmar_ratio", "beta"):
        assert isinstance(m[k], float)
    # rf 미전달 시 동적값(=현재 폴백 3%)이 사용되어야 함
    assert abs(m["rf_annual_used"] - rc.get_dynamic_risk_free_rate()) < 1e-9


def test_advanced_metrics_explicit_rf_respected():
    rets = [0.001 * ((-1) ** i) for i in range(100)]
    m = rc.calculate_advanced_metrics(rets, rf_annual=0.05)
    assert abs(m["rf_annual_used"] - 0.05) < 1e-9
