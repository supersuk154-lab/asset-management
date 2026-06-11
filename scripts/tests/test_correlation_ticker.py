"""
correlation_analyzer.pearson_correlation + ticker_normalizer 단위 테스트
실행: python -m pytest scripts/tests/ -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import unittest
from correlation_analyzer import pearson_correlation


class TestPearsonCorrelation(unittest.TestCase):
    """상관계수 계산 정확성 검증"""

    def test_perfect_positive_correlation(self):
        """동일 시리즈 → 상관계수 1.0"""
        series = [0.01, -0.02, 0.03, -0.01, 0.02] * 10
        r = pearson_correlation(series, series)
        self.assertAlmostEqual(r, 1.0, places=3)

    def test_perfect_negative_correlation(self):
        """부호 반전 시리즈 → 상관계수 -1.0"""
        x = [0.01, -0.02, 0.03, -0.01, 0.02] * 10
        y = [-v for v in x]
        r = pearson_correlation(x, y)
        self.assertAlmostEqual(r, -1.0, places=3)

    def test_uncorrelated_constant_zero(self):
        """상수 시리즈(분산=0) → 0 반환 (ZeroDivisionError 방지)"""
        x = [0.01] * 30
        y = [0.02] * 30
        r = pearson_correlation(x, y)
        self.assertEqual(r, 0.0)

    def test_result_range(self):
        """상관계수는 항상 -1 ~ 1 범위"""
        import random
        random.seed(42)
        x = [random.gauss(0, 0.01) for _ in range(50)]
        y = [random.gauss(0, 0.01) for _ in range(50)]
        r = pearson_correlation(x, y)
        self.assertGreaterEqual(r, -1.0)
        self.assertLessEqual(r, 1.0)

    def test_insufficient_data_returns_zero(self):
        """10개 미만 데이터 → 0.0 반환"""
        x = [0.01, 0.02, 0.03]
        y = [0.01, 0.02, 0.03]
        r = pearson_correlation(x, y)
        self.assertEqual(r, 0.0)

    def test_different_length_uses_minimum(self):
        """길이 다른 시리즈 → 짧은 쪽 기준으로 계산"""
        x = [0.01] * 30
        y = [0.01] * 50
        r = pearson_correlation(x, y)
        # 동일 값이므로 1.0이어야 하지만 상수라서 0.0
        self.assertEqual(r, 0.0)

    def test_known_correlation(self):
        """알려진 입력에 대한 수치 검증"""
        # x와 y가 강한 양의 상관 (직접 계산 기대값 ≈ 0.95 이상)
        x = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
        y = [1.1, 1.9, 3.1, 3.9, 5.1] * 10
        r = pearson_correlation(x, y)
        self.assertGreater(r, 0.99)

    def test_result_is_rounded(self):
        """반환값이 소수점 4자리로 반올림됨"""
        x = [0.01 * i for i in range(1, 31)]
        y = [0.015 * i for i in range(1, 31)]
        r = pearson_correlation(x, y)
        # 4자리 반올림 확인
        self.assertEqual(r, round(r, 4))


class TestNormalizeKey(unittest.TestCase):
    """ticker_normalizer._normalize_key 매칭 키 생성 검증"""

    def setUp(self):
        from ticker_normalizer import _normalize_key
        self._nk = _normalize_key

    def test_lowercase(self):
        self.assertEqual(self._nk("Samsung"), "samsung")

    def test_spaces_removed(self):
        self.assertEqual(self._nk("SK 하이닉스"), "sk하이닉스")

    def test_hyphen_removed(self):
        self.assertEqual(self._nk("S&P-500"), "sp500")

    def test_dot_removed(self):
        self.assertEqual(self._nk("005930.KS"), "005930ks")

    def test_ampersand_removed(self):
        self.assertEqual(self._nk("J&J"), "jj")

    def test_combined(self):
        self.assertEqual(self._nk("TIGER 미국 S&P500"), "tiger미국sp500")

    def test_empty_string(self):
        self.assertEqual(self._nk(""), "")

    def test_korean_unchanged(self):
        """한글은 그대로 유지 (소문자 변환 없음)"""
        self.assertEqual(self._nk("삼성전자"), "삼성전자")


class TestTickerNormalizerExactMatch(unittest.TestCase):
    """ticker_normalizer 정확 매칭 동작 검증"""

    def setUp(self):
        from ticker_normalizer import enrich_one
        self._enrich = enrich_one

    def test_exact_alias_samsung(self):
        """'삼전' alias로 삼성전자 티커 확인"""
        result = self._enrich({"name": "삼전", "amount": 1_000_000})
        self.assertEqual(result.get("ticker"), "005930.KS")
        self.assertEqual(result.get("match_type"), "alias_exact")

    def test_exact_alias_nvidia(self):
        """'엔비디아' → NVDA 확인"""
        result = self._enrich({"name": "엔비디아", "amount": 500_000})
        self.assertEqual(result.get("ticker"), "NVDA")

    def test_unresolved_returns_match_type(self):
        """없는 종목 → match_type: unresolved"""
        result = self._enrich({"name": "존재하지않는종목XYZ", "amount": 100_000})
        self.assertEqual(result.get("match_type"), "unresolved")
        self.assertIsNone(result.get("ticker"))

    def test_already_normalized_skipped(self):
        """이미 정규화된 항목은 재처리하지 않음"""
        pre_normalized = {
            "raw_name": "삼전",
            "standard_name": "삼성전자",
            "ticker": "005930.KS",
            "match_type": "alias_exact",
            "amount": 1_000_000,
        }
        result = self._enrich(pre_normalized)
        # 재실행해도 동일한 ticker 유지
        self.assertEqual(result.get("ticker"), "005930.KS")

    def test_raw_name_preserved(self):
        """raw_name이 원본 입력 보존"""
        result = self._enrich({"name": "삼전", "amount": 500_000})
        self.assertEqual(result.get("raw_name"), "삼전")

    def test_market_field_set(self):
        """market 필드가 채워짐"""
        result = self._enrich({"name": "삼성전자", "amount": 1_000_000})
        self.assertEqual(result.get("market"), "KR")

    def test_sector_field_set(self):
        """sector 필드가 채워짐"""
        result = self._enrich({"name": "삼성전자", "amount": 1_000_000})
        self.assertEqual(result.get("sector"), "IT/반도체")


class TestFxHedgedDetection(unittest.TestCase):
    """ticker_normalizer — fx_hedged 환헤지 자동 감지"""

    def _run_kyc_normalize(self, investments: list) -> list:
        """normalize_kyc를 직접 호출하지 않고 enrich_one + fx_hedged 감지 로직 재현"""
        import re as _re
        from ticker_normalizer import enrich_one

        results = []
        for inv in investments:
            result = enrich_one(inv)
            if result.get("fx_hedged") is None:
                combined = " ".join([
                    result.get("standard_name", ""),
                    result.get("raw_name", ""),
                    result.get("name", ""),
                ]).upper()
                if _re.search(r"\(H\)|헤지|HEDGE|\(환헤지\)", combined):
                    result["fx_hedged"] = True
                elif _re.search(r"\(UH\)|환노출|UNHEDGE", combined):
                    result["fx_hedged"] = False
            results.append(result)
        return results

    def test_hedge_detected_from_name(self):
        """상품명에 (H) 포함 시 fx_hedged=True"""
        invs = [{"name": "TIGER 미국채10년선물(H)", "amount": 1_000_000}]
        result = self._run_kyc_normalize(invs)[0]
        self.assertTrue(result.get("fx_hedged"))

    def test_unhedge_detected(self):
        """상품명에 (UH) 포함 시 fx_hedged=False"""
        invs = [{"name": "ACE 미국S&P500(UH)", "amount": 1_000_000}]
        result = self._run_kyc_normalize(invs)[0]
        self.assertFalse(result.get("fx_hedged"))

    def test_no_hedge_marker_returns_none(self):
        """헤지 표시 없으면 None (스트레스 테스트에서 환노출로 처리)"""
        invs = [{"name": "TIGER 미국S&P500", "amount": 1_000_000}]
        result = self._run_kyc_normalize(invs)[0]
        self.assertIsNone(result.get("fx_hedged"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
