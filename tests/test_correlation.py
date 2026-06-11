# -*- coding: utf-8 -*-
"""correlation_analyzer.py 단위 테스트 — 확장된 자산군 기반 정적 상관계수 fallback.

특히 2026-05-30 적용 시 교정한 '양방향 조회' 버그(계획서 tuple(sorted()) 버그)를
회귀 방지로 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import correlation_analyzer as ca

SKIP_REASON = None


def test_same_class_kor_eq():
    # 두 국내주식 섹터 → KOR_EQ 동조화 0.85
    assert ca.get_static_correlation("IT/반도체", "금융") == 0.85


def test_same_class_us_eq():
    assert ca.get_static_correlation("미국/IT", "미국/S&P500ETF") == 0.80


def test_cross_country_diversification():
    # 국내주식 ↔ 미국주식 = 0.50 (국가 간 분산)
    assert ca.get_static_correlation("IT/반도체", "미국/S&P500ETF") == 0.50


def test_bond_negative_correlation_bidirectional():
    # ★ 버그 수정 회귀 테스트: 정렬 키 조회였다면 0.20 으로 틀렸을 케이스
    assert ca.get_static_correlation("IT/반도체", "단기채") == -0.05
    assert ca.get_static_correlation("단기채", "IT/반도체") == -0.05   # 역순도 동일


def test_us_bond_strong_negative():
    assert ca.get_static_correlation("IT/반도체", "미국/국채ETF") == -0.25


def test_gold_reverse_order_lookup():
    # (GOLD, KOR_EQ) 는 dict 에 (KOR_EQ, GOLD) 로만 정의 → 역순 조회 동작 확인
    assert ca.get_static_correlation("금/원자재", "IT/반도체") == 0.10
    assert ca.get_static_correlation("미국/S&P500ETF", "금/원자재") == 0.15


def test_unknown_sectors_default_to_kor_eq():
    # 미매핑 섹터 두 개 → 둘 다 KOR_EQ → 동일군 0.85
    assert ca.get_static_correlation("듣보잡A", "듣보잡B") == 0.85


def test_cross_class_undefined_default_020():
    # KOR_EQ ↔ CMA 는 정의 안 됨 → 교차 기본값 0.20
    assert ca.get_static_correlation("IT/반도체", "현금") == 0.20


def test_correlation_range_valid():
    # 모든 반환값은 [-1, 1] 범위
    sectors = ["IT/반도체", "미국/IT", "단기채", "미국/국채ETF", "금/원자재", "현금", "미지섹터"]
    for a in sectors:
        for b in sectors:
            r = ca.get_static_correlation(a, b)
            assert -1.0 <= r <= 1.0
