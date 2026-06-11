"""
risk_calculator.py 핵심 계산 함수 단위 테스트
실행: python -m pytest scripts/tests/ -v
또는: python scripts/tests/test_risk_calculator.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import unittest
from risk_calculator import (
    calculate_advanced_metrics,
    run_stress_test,
    calc_glide_path_target,
    get_dynamic_risk_free_rate,
    SECTOR_TO_STRESS_ASSET,
)


class TestCalculateAdvancedMetrics(unittest.TestCase):

    def _flat_returns(self, daily_rate: float, n: int = 252) -> list:
        return [daily_rate] * n

    def test_positive_returns_sharpe_positive(self):
        rets = self._flat_returns(0.001)
        result = calculate_advanced_metrics(rets)
        self.assertGreater(result["sharpe_ratio"], 0)

    def test_negative_returns_sharpe_negative(self):
        rets = self._flat_returns(-0.001)
        result = calculate_advanced_metrics(rets)
        self.assertLess(result["sharpe_ratio"], 0)

    def test_mdd_zero_for_flat_positive(self):
        rets = self._flat_returns(0.001)
        result = calculate_advanced_metrics(rets)
        self.assertAlmostEqual(result["mdd_pct"], 0.0, places=2)

    def test_mdd_negative_for_declining(self):
        rets = [0.01] * 100 + [-0.02] * 50 + [0.0] * 102
        result = calculate_advanced_metrics(rets)
        self.assertLess(result["mdd_pct"], 0)

    def test_beta_one_when_no_benchmark(self):
        rets = self._flat_returns(0.001)
        result = calculate_advanced_metrics(rets)
        self.assertAlmostEqual(result["beta"], 1.0)

    def test_beta_computed_with_benchmark(self):
        rets  = self._flat_returns(0.002)
        bench = self._flat_returns(0.001)
        result = calculate_advanced_metrics(rets, bench)
        self.assertIsInstance(result["beta"], float)
        self.assertNotEqual(result["beta"], 1.0)

    def test_insufficient_data_returns_note(self):
        result = calculate_advanced_metrics([0.01] * 10)
        self.assertIn("note", result)
        self.assertNotIn("sharpe_ratio", result)

    def test_all_required_keys_present(self):
        rets = self._flat_returns(0.0005)
        result = calculate_advanced_metrics(rets)
        for key in ("sharpe_ratio", "sortino_ratio", "mdd_pct", "calmar_ratio", "beta", "annualized_return_pct"):
            self.assertIn(key, result, f"누락된 키: {key}")

    def test_annualized_return_reasonable(self):
        daily = 0.0003  # 약 7.8% 연간 수익률
        rets = self._flat_returns(daily)
        result = calculate_advanced_metrics(rets)
        ann = result["annualized_return_pct"]
        self.assertGreater(ann, 5.0)
        self.assertLess(ann, 15.0)


class TestRunStressTest(unittest.TestCase):

    def _make_inv(self, sector: str, market: str, amount: int) -> dict:
        return {"sector": sector, "market": market, "amount": amount}

    def test_empty_investments_returns_empty(self):
        self.assertEqual(run_stress_test([], 0), {})

    def test_three_scenarios_returned(self):
        invs = [self._make_inv("IT/반도체", "KR", 1_000_000)]
        result = run_stress_test(invs, 1_000_000)
        self.assertEqual(len(result), 3)
        self.assertIn("2008_금융위기", result)
        self.assertIn("2020_코로나", result)
        self.assertIn("2022_금리인상", result)

    def test_drawdown_negative(self):
        invs = [self._make_inv("IT/반도체", "KR", 1_000_000)]
        result = run_stress_test(invs, 1_000_000)
        for scenario in result.values():
            self.assertLess(scenario["portfolio_drawdown_pct"], 0)

    def test_kr_etf_maps_to_korean_stock_not_us(self):
        """BUG-FIX 검증: KR_ETF market이 미국주식으로 오분류되지 않는지 확인"""
        bond_etf = self._make_inv("국내/단기채ETF", "KR_ETF", 1_000_000)
        kr_etf   = self._make_inv("국내/대형주ETF", "KR_ETF", 1_000_000)
        result_bond = run_stress_test([bond_etf], 1_000_000)
        result_kr   = run_stress_test([kr_etf],   1_000_000)

        # 채권 ETF는 채권 시나리오(낙폭 작음)로 처리돼야 함
        bond_2008 = result_bond["2008_금융위기"]["portfolio_drawdown_pct"]
        kr_2008   = result_kr["2008_금융위기"]["portfolio_drawdown_pct"]
        us_stock_2008 = -55.0  # 미국주식 낙폭
        self.assertGreater(bond_2008, us_stock_2008, "채권ETF가 미국주식으로 오분류됨")
        self.assertLess(kr_2008, bond_2008, "국내주식ETF가 채권보다 낙폭이 커야 함")

    def test_loss_proportional_to_investment(self):
        inv_small = [self._make_inv("IT/반도체", "KR", 1_000_000)]
        inv_large = [self._make_inv("IT/반도체", "KR", 10_000_000)]
        r_small = run_stress_test(inv_small, 1_000_000)
        r_large = run_stress_test(inv_large, 10_000_000)
        self.assertAlmostEqual(
            r_small["2008_금융위기"]["portfolio_drawdown_pct"],
            r_large["2008_금융위기"]["portfolio_drawdown_pct"],
        )


class TestCalcGlidePathTarget(unittest.TestCase):

    def test_young_investor_high_risky(self):
        result = calc_glide_path_target(25, "retirement")
        self.assertEqual(result["target_risky_pct"], 90.0)

    def test_senior_investor_low_risky(self):
        result = calc_glide_path_target(65, "retirement")
        self.assertEqual(result["target_risky_pct"], 30.0)

    def test_short_term_goal_reduces_risky(self):
        base    = calc_glide_path_target(35, "retirement")
        short   = calc_glide_path_target(35, "short_term")
        self.assertLess(short["target_risky_pct"], base["target_risky_pct"])

    def test_total_always_100(self):
        for age in (25, 35, 45, 55, 65):
            result = calc_glide_path_target(age, "retirement")
            total = result["target_risky_pct"] + result["target_safe_pct"]
            self.assertAlmostEqual(total, 100.0, places=5, msg=f"합계 오류 (age={age})")

    def test_risky_within_bounds(self):
        for age in range(20, 76, 5):
            result = calc_glide_path_target(age, "retirement")
            self.assertGreaterEqual(result["target_risky_pct"], 20.0)
            self.assertLessEqual(result["target_risky_pct"], 90.0)

    def test_upper_lower_limits_symmetric(self):
        # age=50: target=66% → upper=76%, lower=56% (90/20 클램프 미적용 구간)
        result = calc_glide_path_target(50, "retirement")
        target = result["target_risky_pct"]
        self.assertAlmostEqual(result["upper_limit"] - target, 10.0, delta=0.1)
        self.assertAlmostEqual(target - result["lower_limit"], 10.0, delta=0.1)

    # ── 직업형 인적자본 보정 테스트 ──

    def test_freelancer_gets_lower_risky_than_salaried(self):
        """자영업/프리랜서는 급여소득자보다 위험자산 비중이 낮아야 함"""
        salaried   = calc_glide_path_target(40, "retirement", "급여소득자")
        freelancer = calc_glide_path_target(40, "retirement", "자영업/프리랜서")
        self.assertLess(freelancer["target_risky_pct"],
                        salaried["target_risky_pct"])
        self.assertEqual(freelancer["job_type_adjustment"], -10)
        self.assertEqual(salaried["job_type_adjustment"], 0)

    def test_freelancer_short_term_no_extra_correction(self):
        """단기 목표는 이미 × 0.6 보정이므로 직업형 보정 추가 없음"""
        salaried   = calc_glide_path_target(35, "short_term", "급여소득자")
        freelancer = calc_glide_path_target(35, "short_term", "자영업/프리랜서")
        self.assertEqual(salaried["target_risky_pct"],
                         freelancer["target_risky_pct"])
        self.assertEqual(freelancer["job_type_adjustment"], 0)

    def test_freelancer_risky_still_within_bounds(self):
        """직업형 보정 후에도 위험자산 비중이 20~90% 범위 내에 있어야 함"""
        for age in (25, 35, 45, 55, 65):
            result = calc_glide_path_target(age, "retirement", "자영업/프리랜서")
            self.assertGreaterEqual(result["target_risky_pct"], 20.0)
            self.assertLessEqual(result["target_risky_pct"], 90.0)

    def test_job_type_adjustment_field_present(self):
        """반환 dict에 job_type_adjustment 키가 항상 존재해야 함"""
        result = calc_glide_path_target(35, "retirement")
        self.assertIn("job_type_adjustment", result)


class TestFXCushionStressTest(unittest.TestCase):
    """#1 FX 환쿠션 효과 — 환노출 달러 자산의 위기 시 원화 손실 완충"""

    def _inv(self, sector: str, market: str, amount: int, fx_hedged=None) -> dict:
        d = {"sector": sector, "market": market, "amount": amount}
        if fx_hedged is not None:
            d["fx_hedged"] = fx_hedged
        return d

    def test_unhedged_us_stock_lower_loss_than_raw(self):
        """환노출 미국주식은 원/달러 급등으로 원화 손실이 완화돼야 함"""
        invs_unhedged = [self._inv("미국/IT", "US", 1_000_000, fx_hedged=False)]
        invs_hedged   = [self._inv("미국/IT", "US", 1_000_000, fx_hedged=True)]

        r_uh = run_stress_test(invs_unhedged, 1_000_000)
        r_h  = run_stress_test(invs_hedged,   1_000_000)

        # 2008: 환노출이 환헤지보다 손실이 작아야 함 (FX 완충)
        uh_2008 = r_uh["2008_금융위기"]["portfolio_drawdown_pct"]
        h_2008  = r_h ["2008_금융위기"]["portfolio_drawdown_pct"]
        self.assertGreater(uh_2008, h_2008, "환노출이 환헤지보다 손실이 커야 할 이유 없음 — 부호 반전 확인")
        # uh_2008은 음수, h_2008도 음수 — 환노출이 손실이 "덜" 함(더 작은 절대값)
        self.assertGreater(uh_2008, h_2008)  # uh > h → uh가 덜 빠짐

    def test_fx_cushion_2008_math(self):
        """2008 FX 완충 수식 검증: (1 - 0.55) × (1 + 0.40) - 1 = -37%"""
        invs = [self._inv("미국/IT", "US", 1_000_000, fx_hedged=False)]
        result = run_stress_test(invs, 1_000_000)
        dd = result["2008_금융위기"]["portfolio_drawdown_pct"]
        expected = ((1 - 0.55) * (1 + 0.40) - 1) * 100   # -37.0%
        self.assertAlmostEqual(dd, expected, delta=0.5)

    def test_krw_assets_no_fx_cushion(self):
        """원화 자산(한국주식)은 FX 완충 없이 그대로 적용"""
        invs_kr = [self._inv("IT/반도체", "KR", 1_000_000)]
        r_kr = run_stress_test(invs_kr, 1_000_000)
        # 한국주식 2008 낙폭 = -54% 그대로
        self.assertAlmostEqual(r_kr["2008_금융위기"]["portfolio_drawdown_pct"], -54.0, delta=0.5)

    def test_global_stock_partial_cushion(self):
        """글로벌주식은 USD비중 60%만큼 부분 완충"""
        invs_global = [self._inv("글로벌ETF", "US", 1_000_000, fx_hedged=False)]
        r = run_stress_test(invs_global, 1_000_000)
        dd = r["2008_금융위기"]["portfolio_drawdown_pct"]
        expected = ((1 - 0.56) * (1 + 0.40 * 0.6) - 1) * 100
        self.assertAlmostEqual(dd, expected, delta=0.5)


class TestHardCapGlidePath(unittest.TestCase):
    """#4 단기 목표 Hard Cap — goal_years_remaining 기반"""

    def test_3yr_goal_capped_at_20(self):
        """3년 이내 목표: 위험자산 최대 20%"""
        result = calc_glide_path_target(25, "housing", goal_years_remaining=2)
        self.assertEqual(result["target_risky_pct"], 20.0)
        self.assertTrue(result["hard_cap_applied"])
        self.assertEqual(result["hard_cap_value"], 20.0)

    def test_5yr_goal_capped_at_40(self):
        """5년 목표: 위험자산 최대 40%"""
        result = calc_glide_path_target(25, "short_term", goal_years_remaining=5)
        self.assertEqual(result["target_risky_pct"], 40.0)
        self.assertTrue(result["hard_cap_applied"])

    def test_10yr_goal_uses_multiplier(self):
        """10년 목표: 하드캡 없이 기존 0.6 배율 적용"""
        result = calc_glide_path_target(25, "housing", goal_years_remaining=10)
        self.assertFalse(result["hard_cap_applied"])
        self.assertAlmostEqual(result["target_risky_pct"], 90 * 0.6, delta=0.1)

    def test_no_years_fallback_to_multiplier(self):
        """goal_years_remaining 없으면 기존 0.6 배율 fallback"""
        result_no_years = calc_glide_path_target(25, "housing")
        result_long     = calc_glide_path_target(25, "housing", goal_years_remaining=10)
        self.assertAlmostEqual(result_no_years["target_risky_pct"],
                               result_long["target_risky_pct"], delta=0.1)

    def test_total_always_100_with_hardcap(self):
        """하드캡 적용 후에도 safe + risky = 100"""
        result = calc_glide_path_target(30, "short_term", goal_years_remaining=1)
        total = result["target_risky_pct"] + result["target_safe_pct"]
        self.assertAlmostEqual(total, 100.0, places=5)

    def test_retirement_goal_no_hardcap(self):
        """은퇴 목표는 goal_years_remaining과 무관하게 하드캡 없음"""
        result = calc_glide_path_target(30, "retirement", goal_years_remaining=3)
        self.assertFalse(result["hard_cap_applied"])


class TestSectorToStressMapping(unittest.TestCase):
    """MISMATCH-01 수정 검증: 섹터 매핑 누락 없는지 확인"""

    EXPECTED = {
        "IT/반도체": "한국주식", "IT/플랫폼": "한국주식",
        "바이오/제약": "한국주식", "금융/은행": "한국주식",
        "조선": "한국주식", "방산/항공": "한국주식",
        "철강/소재": "한국주식", "화학/배터리": "한국주식",
        "국내/대형주ETF": "한국주식",
        "미국/반도체": "미국주식", "미국/전기차": "미국주식",
        "국내/단기채ETF": "채권", "미국/국채ETF": "채권",
        "혼합/채권혼합ETF": "채권",
        "금/원자재": "금", "현금": "현금",
    }

    def test_all_sectors_mapped(self):
        for sector, expected in self.EXPECTED.items():
            self.assertIn(sector, SECTOR_TO_STRESS_ASSET,
                          f"SECTOR_TO_STRESS_ASSET에 '{sector}' 누락")
            self.assertEqual(SECTOR_TO_STRESS_ASSET[sector], expected,
                             f"'{sector}' 매핑 오류: {SECTOR_TO_STRESS_ASSET[sector]} != {expected}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
