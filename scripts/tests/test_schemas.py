"""
schemas.py Pydantic 모델 단위 테스트
실행: python -m pytest scripts/tests/ -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from pydantic import ValidationError
from schemas import (
    KYCOutput, PortfolioDesignerOutput, StockPlanOutput,
    StockProduct, RiskScorerOutput, ReviewerOutput,
    CorrelationAnalysisOutput, QuantitativeMetrics,
    GoalType, TOLERANCE_MIN_KRW, TOLERANCE_RATE,
    _LOCKED_ACCOUNT_TYPES, _ISA_ACCOUNT_TYPES,
)


class TestPortfolioDesignerOutput(unittest.TestCase):

    def _valid(self, safe=40.0, risky=60.0, core=70.0, sat=30.0):
        return PortfolioDesignerOutput(
            safe_pct=safe, risky_pct=risky,
            core_pct=core, satellite_pct=sat,
            plain_language_ips="테스트 IPS",
        )

    def test_valid_100_sum(self):
        obj = self._valid()
        self.assertAlmostEqual(obj.safe_pct + obj.risky_pct, 100.0)

    def test_fails_if_not_100(self):
        with self.assertRaises(ValidationError):
            self._valid(safe=40.0, risky=55.0)

    def test_fails_if_core_sat_not_100(self):
        with self.assertRaises(ValidationError):
            self._valid(core=70.0, sat=25.0)


class TestStockPlanOutput(unittest.TestCase):

    def _product(self, name: str, amount: int, account: str = "ISA") -> StockProduct:
        return StockProduct(name=name, account_type=account, monthly_amount=amount)

    def test_valid_totals_match(self):
        plan = StockPlanOutput(
            client_id="test", created_at="2026-06-02",
            safe_products=[self._product("파킹통장", 200_000, "일반계좌")],
            core_products=[self._product("KODEX200", 700_000, "IRP")],
            satellite_products=[self._product("NVDA", 100_000, "일반계좌")],
            total_monthly=1_000_000,
        )
        self.assertEqual(plan.total_monthly, 1_000_000)

    def test_fails_when_total_mismatch(self):
        with self.assertRaises(ValidationError):
            StockPlanOutput(
                client_id="test", created_at="2026-06-02",
                safe_products=[self._product("파킹통장", 200_000, "일반계좌")],
                core_products=[self._product("KODEX200", 500_000, "IRP")],
                satellite_products=[],
                total_monthly=1_000_000,
            )

    def test_liquidity_lock_short_term_blocks_isa(self):
        with self.assertRaises(ValidationError):
            StockPlanOutput(
                client_id="test", created_at="2026-06-02",
                gbi_goal_type="short_term",
                safe_products=[self._product("파킹통장", 500_000, "ISA")],
                core_products=[],
                satellite_products=[],
                total_monthly=500_000,
            )

    def test_liquidity_lock_retirement_allows_isa(self):
        plan = StockPlanOutput(
            client_id="test", created_at="2026-06-02",
            gbi_goal_type="retirement",
            safe_products=[self._product("단기채ETF", 500_000, "ISA")],
            core_products=[],
            satellite_products=[],
            total_monthly=500_000,
        )
        self.assertEqual(plan.gbi_goal_type, "retirement")


class TestRiskScorerOutput(unittest.TestCase):

    def _valid_details(self, scores=(22, 18, 20, 12)):
        keys = ["cashflow", "behavioral_gap", "emergency_fund", "diversification"]
        return {k: {"score": s, "max": 25, "comment": "테스트"} for k, s in zip(keys, scores)}

    def test_valid_score_matches_sum(self):
        obj = RiskScorerOutput(
            total_score=72, grade="🟡",
            details=self._valid_details((22, 18, 20, 12)),
            penalty_score=0,
            urgent_actions=["테스트"],
            fact_bomb="테스트",
        )
        self.assertEqual(obj.total_score, 72)

    def test_fails_when_score_mismatch(self):
        with self.assertRaises(ValidationError):
            RiskScorerOutput(
                total_score=80,
                grade="🟡",
                details=self._valid_details((22, 18, 20, 12)),
                penalty_score=0,
                urgent_actions=["테스트"],
                fact_bomb="테스트",
            )

    def test_penalty_reduces_score(self):
        obj = RiskScorerOutput(
            total_score=57,
            grade="🔴",
            details=self._valid_details((22, 18, 20, 12)),
            penalty_score=-15,
            urgent_actions=["테스트"],
            fact_bomb="테스트",
        )
        self.assertEqual(obj.total_score, 57)

    def test_invalid_grade_fails(self):
        with self.assertRaises(ValidationError):
            RiskScorerOutput(
                total_score=72, grade="🔵",
                details=None, penalty_score=0,
                urgent_actions=["테스트"], fact_bomb="테스트",
            )


class TestReviewerOutput(unittest.TestCase):

    def test_fail_verdict_false_confirmed(self):
        with self.assertRaises(ValidationError):
            ReviewerOutput(
                client_id="test", reviewed_at="2026-06-02",
                verdict="FAIL", checks={}, warnings=[],
                report_confirmed=True,
            )

    def test_pass_verdict_true_confirmed(self):
        obj = ReviewerOutput(
            client_id="test", reviewed_at="2026-06-02",
            verdict="PASS", checks={}, warnings=[],
            report_confirmed=True,
        )
        self.assertTrue(obj.report_confirmed)


class TestQuantitativeMetricsInCorrelation(unittest.TestCase):
    """DESIGN-01: CorrelationAnalysisOutput.portfolio_metrics가 QuantitativeMetrics로 검증되는지 확인"""

    def test_portfolio_metrics_validated_as_quantitative_metrics(self):
        obj = CorrelationAnalysisOutput(
            client_id="test", analyzed_at="2026-06-02",
            portfolio_diversification_score=80,
            pseudo_diversification_detected=False,
            high_correlation_pairs=[],
            portfolio_metrics={
                "sharpe_ratio": 1.5, "sortino_ratio": 2.1,
                "mdd_pct": -12.5, "calmar_ratio": 0.8,
                "beta": 0.95, "annualized_return_pct": 8.3,
                "data_days": 252, "rf_annual_used": 0.03,
                "note": "실제 보유 종목 수익률 기반",
            },
        )
        metrics = obj.portfolio_metrics
        self.assertIsInstance(metrics, QuantitativeMetrics)
        self.assertEqual(metrics.sharpe_ratio, 1.5)
        self.assertEqual(metrics.mdd_pct, -12.5)

    def test_data_days_extra_field_ignored_gracefully(self):
        obj = CorrelationAnalysisOutput(
            client_id="test", analyzed_at="2026-06-02",
            portfolio_diversification_score=50,
            pseudo_diversification_detected=False,
            high_correlation_pairs=[],
            portfolio_metrics={"note": "데이터 부족"},
        )
        self.assertIsNotNone(obj.portfolio_metrics)


class TestGoalTypeEnum(unittest.TestCase):
    """GoalType str Enum — 하위 호환성 및 동작 검증"""

    def test_string_coerced_to_enum(self):
        """Pydantic이 "retirement" 문자열을 GoalType.RETIREMENT로 변환해야 함"""
        plan = StockPlanOutput(
            client_id="test", created_at="2026-06-02",
            gbi_goal_type="retirement",
            safe_products=[StockProduct(name="파킹", account_type="일반계좌", monthly_amount=500_000)],
            core_products=[],
            satellite_products=[],
            total_monthly=500_000,
        )
        self.assertEqual(plan.gbi_goal_type, GoalType.RETIREMENT)
        self.assertEqual(plan.gbi_goal_type, "retirement")   # str 비교도 True

    def test_enum_values_match_strings(self):
        self.assertEqual(GoalType.RETIREMENT, "retirement")
        self.assertEqual(GoalType.HOUSING,    "housing")
        self.assertEqual(GoalType.SHORT_TERM, "short_term")
        self.assertEqual(GoalType.UNSET,      "미설정")

    def test_invalid_goal_type_raises(self):
        with self.assertRaises(ValidationError):
            StockPlanOutput(
                client_id="test", created_at="2026-06-02",
                gbi_goal_type="invalid_goal",
                safe_products=[StockProduct(name="파킹", account_type="일반계좌", monthly_amount=100_000)],
                core_products=[], satellite_products=[],
                total_monthly=100_000,
            )


class TestConstants(unittest.TestCase):
    """상수 및 frozenset 검증"""

    def test_tolerance_constants(self):
        self.assertEqual(TOLERANCE_MIN_KRW, 10_000)
        self.assertAlmostEqual(TOLERANCE_RATE, 0.01)

    def test_locked_account_types_contains_expected(self):
        for acc in ("IRP", "연금저축", "연금저축펀드"):
            self.assertIn(acc, _LOCKED_ACCOUNT_TYPES)

    def test_locked_does_not_contain_false_positive(self):
        # "일반계좌(연금저축 아님)" 같은 LLM 변형은 frozenset에 없어야 함
        self.assertNotIn("일반계좌(연금저축 아님)", _LOCKED_ACCOUNT_TYPES)
        self.assertNotIn("일반계좌", _LOCKED_ACCOUNT_TYPES)

    def test_isa_account_types(self):
        self.assertIn("ISA", _ISA_ACCOUNT_TYPES)
        self.assertNotIn("일반계좌", _ISA_ACCOUNT_TYPES)


class TestSafeReadJson(unittest.TestCase):
    """utils.safe_read_json 동작 검증"""

    def test_returns_empty_dict_for_missing_file(self):
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils import safe_read_json
        result = safe_read_json(Path("/nonexistent/path/file.json"))
        self.assertEqual(result, {})

    def test_returns_default_val_for_missing_file(self):
        from utils import safe_read_json
        result = safe_read_json(Path("/nonexistent.json"), default_val={"date": "없음"})
        self.assertEqual(result["date"], "없음")

    def test_reads_valid_json(self):
        import tempfile, json
        from utils import safe_read_json
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            json.dump({"key": "value"}, f)
            tmp_path = Path(f.name)
        try:
            result = safe_read_json(tmp_path)
            self.assertEqual(result["key"], "value")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_returns_default_on_corrupt_json(self):
        import tempfile
        from utils import safe_read_json
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            f.write("{broken json")
            tmp_path = Path(f.name)
        try:
            result = safe_read_json(tmp_path, default_val={"fallback": True})
            self.assertTrue(result.get("fallback"))
        finally:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
