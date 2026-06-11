from enum import Enum

from pydantic import BaseModel, Field, model_validator
from typing import Dict, List, Literal, Optional
import math


# ─────────────────────────────────────────────
# Enum — 파이프라인 전체에서 공유하는 열거형
# ─────────────────────────────────────────────

class GoalType(str, Enum):
    """투자 목표 유형. str 상속이므로 "retirement" 같은 문자열과 직접 비교 가능."""
    RETIREMENT     = "retirement"
    HOUSING        = "housing"
    EDUCATION_GIFT = "education_gift"
    SHORT_TERM     = "short_term"
    CUSTOM         = "custom"
    UNSET          = "미설정"


# ─────────────────────────────────────────────
# 검증 상수 — 매직 넘버 제거
# ─────────────────────────────────────────────

TOLERANCE_MIN_KRW: int   = 10_000   # 금액 검증 최소 허용 오차 (원)
TOLERANCE_RATE:    float = 0.01     # 금액 검증 비율 허용 오차 (1%)

# 유동성 잠금 계좌 — 단/중기 목표에 추천 금지
_LOCKED_ACCOUNT_TYPES: frozenset[str] = frozenset({
    "IRP", "연금저축", "연금저축펀드", "연금저축보험", "연금저축신탁",
})
# 단기 목표에 추천 금지 (3년 락업)
_ISA_ACCOUNT_TYPES: frozenset[str] = frozenset({
    "ISA", "ISA계좌", "개인종합자산관리계좌",
})


# ─────────────────────────────────────────────
# 투자 종목 아이템 스키마 (kyc investments 배열용)
# ─────────────────────────────────────────────
class InvestmentItem(BaseModel):
    """kyc.assets.investments 배열의 개별 종목. ticker_normalizer 실행 후 필드 확장됨."""
    raw_name: Optional[str] = Field(None, description="입력 원문 종목명 (정규화 전)")
    name: Optional[str] = Field(None, description="입력 종목명 (raw_name 없을 때 fallback)")
    standard_name: Optional[str] = Field(None, description="정규화 후 표준 종목명")
    ticker: Optional[str] = Field(None, description="Yahoo Finance 티커")
    sector: Optional[str] = Field(None, description="섹터 분류 (예: IT/반도체)")
    market: Optional[str] = Field(None, description="상장 시장 (KR / US 등)")
    amount: int = Field(..., ge=0, description="보유 금액 (원)")
    account_location: str = Field(
        default="일반",
        description="현재 보유 계좌 유형 — ISA / IRP / 연금저축 / 일반"
    )
    match_type: Optional[str] = None
    match_confidence: Optional[int] = None
    needs_review: Optional[bool] = None
    fx_hedged: Optional[bool] = Field(
        None,
        description=(
            "환헤지 여부. True=헤지(H), False=환노출(UH), None=미지정(시장코드로 추정). "
            "스트레스 테스트에서 해외 자산의 환쿠션 효과 계산에 사용. "
            "ticker_normalizer가 상품명의 '(H)'·'헤지' 패턴으로 자동 감지."
        )
    )


# ─────────────────────────────────────────────
# kyc-collector 출력 스키마 (Step 1)
# ─────────────────────────────────────────────
class KYCStatus(BaseModel):
    needs_review: bool
    emergency_fund_missing: bool
    unusual_asset_flag: bool
    risk_conflict: bool
    ticker_normalization: Optional[str] = None
    insurance_gap: Optional[bool] = Field(
        None,
        description="보장 공백 감지 여부 (has_dependents == True 이고 has_protection_insurance == False 인 경우)"
    )


class KYCProfile(BaseModel):
    age_group: str = Field(..., description="예: '30대'")
    age_midpoint: int = Field(..., ge=20, le=75)
    goal: str
    gbi_goal_type: GoalType
    job_type: Literal["급여소득자", "자영업/프리랜서"] = Field(default="급여소득자", description="직업 형태")
    has_dependents: bool = Field(default=False, description="부양가족 유무")
    has_investment_experience: bool = Field(default=False, description="1년 이상 직접 투자 경험 여부")
    uses_recurring_investment: bool = Field(default=False, description="현재 자동 적립식 투자 활용 여부")
    risk_type: Literal["안정형", "중립형", "적극형", "미분류"]
    risk_willingness: Literal["안정형", "중립형", "적극형", "미분류"]
    risk_capacity_score: int = Field(..., ge=0, le=100)
    tax_accounts: List[str]
    esg_exclude: List[str]
    email: Optional[str] = Field(
        None,
        description="고객 이메일 — 재상담 고객 식별 및 리포트 자동 발송용. 소문자 정규화."
    )
    lump_sum_intent_krw: Optional[int] = Field(
        None, ge=0,
        description=(
            "목돈 일시 투자 희망금액 (원). 폼의 '목돈투자희망금액'(만원)을 ×10,000 변환. "
            "portfolio-designer가 비상금 안전선(6개월치 생활비) 차감 후 "
            "실제 투자 가능 금액(lump_sum_investable_krw)을 별도 계산한다. "
            "None 또는 0이면 일시납 플랜 생략."
        )
    )
    goal_years_remaining: Optional[int] = Field(
        None, ge=1, le=50,
        description=(
            "목표 달성까지 남은 기간 (년). 폼의 '목표기간' 컬럼 또는 기타메모에서 파싱. "
            "retirement → 기본값 = 65 - age_midpoint, "
            "housing → 기본값 5, short_term → 기본값 2. "
            "portfolio-designer가 글라이드 패스 정밀 계산에 활용."
        )
    )
    goal_amount_krw: Optional[int] = Field(
        None, ge=0,
        description="목표 달성에 필요한 금액 (원). 폼의 '목표금액'(만원)을 ×10,000 변환."
    )
    loan_interest_rate_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description="실제 대출 금리 (%)"
    )
    has_protection_insurance: Optional[bool] = Field(
        None,
        description="보장성 보험 가입 여부"
    )
    expected_pension_monthly_krw: Optional[int] = Field(
        None, ge=0,
        description="예상 국민연금+퇴직연금 수령 월액 (원)"
    )
    estimated_monthly_expense_krw: Optional[int] = Field(
        None, ge=0,
        description="월 지출 추정액 (원)"
    )


class KYCAssets(BaseModel):
    cash: int = Field(..., ge=0)
    investments: List[InvestmentItem] = Field(
        default_factory=list,
        description="보유 투자 상품 목록 (ticker_normalizer 실행 후 필드 확장)"
    )
    investments_total: int = Field(..., ge=0)
    pension: int = Field(..., ge=0)
    non_liquid_assets: int = Field(default=0, ge=0, description="부동산/전월세 보증금 등 비유동성 자산 (만원*10000)")
    mortgage_debt: int = Field(default=0, ge=0, description="주택담보/전세대출 등 저금리 부채 (만원*10000)")
    high_interest_debt: int = Field(default=0, ge=0, description="신용대출/현금서비스 등 고금리 부채 (만원*10000)")
    debt: int = Field(..., ge=0)
    total_gross: int = Field(..., ge=0)
    net_assets: int

    @model_validator(mode='after')
    def check_gross(self) -> 'KYCAssets':
        expected_gross = self.cash + self.investments_total + self.pension + self.non_liquid_assets
        if abs(expected_gross - self.total_gross) > 10000:  # 1만원 오차 허용
            raise ValueError(
                f"total_gross({self.total_gross:,}) ≠ cash+investments+pension+non_liquid_assets({expected_gross:,}). "
                "kyc-collector 계산 오류를 수정하세요."
            )
        # 부채 세분화 검증: mortgage_debt·high_interest_debt 중 하나라도 입력된 경우에만 합계 교차 확인
        # 둘 다 기본값(0)이면 — 기존 kyc.json처럼 debt만 있는 경우 — 건너뜀 (하위 호환)
        if self.mortgage_debt > 0 or self.high_interest_debt > 0:
            expected_debt = self.mortgage_debt + self.high_interest_debt
            if abs(expected_debt - self.debt) > 10000:
                raise ValueError(
                    f"debt({self.debt:,}) ≠ mortgage_debt+high_interest_debt({expected_debt:,}). "
                    "세분화된 부채 합계와 debt 총액을 일치시키세요."
                )
        return self


class KYCFlags(BaseModel):
    """비상예비비·자산 집중도 등 주의사항 플래그 (orchestrator _print_summary가 읽음)"""
    emergency_months: Optional[float] = Field(
        None, ge=0,
        description="현재 비상예비비로 생활 가능한 개월 수 (목표: 3~6개월)"
    )
    largest_holding_pct: Optional[float] = Field(
        None, ge=0, le=100,
        description="최대 단일 투자 종목 비중 (%) — risk-scorer 분산도 채점 입력값"
    )
    human_capital_note: Optional[str] = Field(None, description="인적 자본 관련 주의사항")
    asset_concentration_note: Optional[str] = Field(
        None, description="특정 종목 집중 주의사항 (30% 초과 시)"
    )
    pseudo_diversification_warning: Optional[str] = Field(
        None, description="가짜 분산 경고 (동일 섹터 60% 초과 시 사전 경고)"
    )


class KYCCashflow(BaseModel):
    """kyc-collector가 생성하는 현금흐름 데이터 (risk-scorer·report-writer가 읽음)"""
    monthly_income: Optional[int] = Field(None, ge=0, description="월 수입 (원). 미공개 시 null")
    monthly_surplus: int = Field(..., ge=0, description="월 여유자금 (원)")
    savings_rate_pct: Optional[float] = Field(None, ge=0, le=100, description="저축률 (%)")
    income_disclosed: bool = Field(default=True, description="월 수입 공개 여부")


class KYCImplicitAssets(BaseModel):
    human_capital_proxy: Optional[int] = Field(None, ge=0, description="인적 자본 추정치 (원)")
    estimated_years_remaining: Optional[int] = Field(None, ge=0, description="잔여 근로 연수")


class KYCImplicitLiabilities(BaseModel):
    annual_living_expenses: Optional[int] = Field(None, ge=0, description="연간 생활비 추정치 (원)")


class KYCExtendedBalanceSheet(BaseModel):
    """kyc-collector가 생성하는 확장 대차대조표 (report-writer GBI 서술에 사용)"""
    explicit_assets_total: int = Field(..., ge=0, description="명시적 자산 합계 (원)")
    implicit_assets: Optional[KYCImplicitAssets] = None
    implicit_liabilities: Optional[KYCImplicitLiabilities] = None
    net_wealth_proxy: Optional[int] = Field(None, description="총 자산 추정치 (인적 자본 포함, 원)")


class KYCOutput(BaseModel):
    client_id: str
    created_at: str  # YYYY-MM-DD
    status: KYCStatus
    profile: KYCProfile
    assets: KYCAssets
    flags: Optional[KYCFlags] = None
    cashflow: Optional[KYCCashflow] = Field(
        None,
        description="현금흐름 데이터 — risk-scorer 지표1·report-writer 변수 치환에 사용"
    )
    extended_balance_sheet: Optional[KYCExtendedBalanceSheet] = Field(
        None,
        description="확장 대차대조표 (인적 자본 포함) — report-writer GBI 서술에 사용"
    )


# ─────────────────────────────────────────────
# macro-analyst 출력 스키마 (Step 2)
# ─────────────────────────────────────────────
class MacroAnalystOutput(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    regime: Literal["위험선호", "중립", "위험회피"] = Field(
        ..., description="현재 시장 국면"
    )
    taa_bias: Literal["주식 비중 확대", "유지", "채권/현금 비중 확대"] = Field(
        ..., description="전술적 자산 배분(TAA) 방향성"
    )
    market_sentiment: Literal["강세장", "중립", "약세장", "극도의 공포", "불확실"]
    summary_for_beginner: str = Field(
        ..., min_length=20,
        description="초보자도 이해할 수 있는 시장 요약 (3~5문장)"
    )
    interest_rate_trend: str = Field(..., description="금리 방향 요약")
    inflation_status: str = Field(..., description="물가 상황 요약")
    bullish_sectors: List[str] = Field(default_factory=list)
    bearish_sectors: List[str] = Field(default_factory=list)
    fear_greed_index: Optional[float] = Field(
        None, ge=0, le=100,
        description="CNN 공포·탐욕 지수 (0~100). fetch_market_data 미실행 시 null"
    )
    taa_adjustment_pct: Optional[float] = Field(
        None, ge=-15.0, le=15.0,
        description=(
            "TAA 정량 조정값 (%p). taa_bias에서 기계적 도출: "
            "'주식 비중 확대' → +5, '유지' → 0, '채권/현금 비중 확대' → -5. "
            "portfolio-designer가 장기 면제 조건 적용 후 글라이드패스 결과에 가감."
        )
    )


# ─────────────────────────────────────────────
# portfolio-designer 출력 스키마 (Step 3)
# ─────────────────────────────────────────────
class PortfolioDesignerOutput(BaseModel):
    # ── 필수 필드 (Pydantic 수학적 강제 대상) ──
    safe_pct: float = Field(..., ge=0, le=100, description="안전 자산 비중 (0~100)")
    risky_pct: float = Field(..., ge=0, le=100, description="위험 자산 비중 (0~100)")
    core_pct: float = Field(..., ge=0, le=100, description="위험자산 내 Core 비중 (0~100)")
    satellite_pct: float = Field(..., ge=0, le=100, description="위험자산 내 Satellite 비중 (0~100)")
    plain_language_ips: str = Field(..., description="평문으로 번역된 IPS")
    nudge_message: Optional[str] = Field(None, description="2030 안정형을 위한 넛지 메시지")
    brake_message: Optional[str] = Field(None, description="5060 적극형을 위한 브레이크 메시지")

    # ── 확장 필드 (report-writer·risk-scorer·reviewer가 참조) ──
    client_id: Optional[str] = Field(None, description="고객 ID")
    designed_at: Optional[str] = Field(None, description="설계 날짜 YYYY-MM-DD")
    investment_policy_statement: Optional[Dict] = Field(
        None,
        description="IPS 원본 객체 (return_objective, risk_limit_mdd, liquidity_needs, time_horizon)"
    )
    glide_path_base: Optional[Dict] = Field(
        None,
        description="글라이드 패스 기본값 (age_midpoint, base_risky_pct, after_clamp_risky_pct)"
    )
    after_personality_adjust: Optional[Dict] = Field(
        None,
        description="성향 보정 후 최종 비율 (safe_pct, risky_pct, adjustment_log) — reviewer V12 교차 확인용"
    )
    risk_conflict_applied: Optional[bool] = Field(
        None, description="risk_conflict 추가 -10%p 적용 여부 — reviewer V14 확인용"
    )
    gbi_sub_portfolios: Optional[Dict] = Field(
        None,
        description="GBI 버킷 설계 (lifestyle_safety_bucket, growth_surplus_bucket) — report-writer 서술용"
    )
    core_satellite: Optional[Dict] = Field(
        None,
        description="Core:Satellite 비율 객체 (core_pct, satellite_pct) — report-writer 치환용"
    )
    macro_adjusted: Optional[bool] = Field(
        None, description="거시경제 반영 추가 조정 여부"
    )
    rebalancing_rule: Optional[str] = Field(
        None,
        description=(
            "리밸런싱 룰북 텍스트 — report-writer {{REBALANCING_RULE}} 치환용. "
            "반드시 plain string. 객체/배열 금지. "
            "예: '연 1회. Watering 우선. 허용 밴드 ±8%...'"
        )
    )
    glide_path_comment: Optional[str] = Field(
        None, description="글라이드 패스 코멘트 — report-writer {{GLIDE_PATH_COMMENT}} 치환용"
    )
    rationale: Optional[str] = Field(None, description="배분 근거 서술")
    lump_sum_plan: Optional[Dict] = Field(
        None,
        description="목돈 일시납 플랜 (intent_krw, investable_krw, shortfall_krw, recommended_dca_months 등)"
    )
    goal_funding: Optional[Dict] = Field(
        None,
        description=(
            "목표 자금 적정성 점검 (Funded Ratio). kyc.profile.goal_amount_krw·goal_years_remaining이 "
            "모두 있을 때만 채움. {goal_amount_krw, years, accumulation_proxy_krw, target_multiple, "
            "status, prescription}. 둘 중 하나라도 없으면 null."
        )
    )

    @model_validator(mode='after')
    def check_total_pct(self) -> 'PortfolioDesignerOutput':
        if not math.isclose(self.safe_pct + self.risky_pct, 100.0, abs_tol=1e-5):
            raise ValueError(
                f"safe_pct({self.safe_pct}) + risky_pct({self.risky_pct}) "
                f"= {self.safe_pct + self.risky_pct:.4f} ≠ 100. "
                "safe_pct와 risky_pct 합계를 100으로 맞추세요."
            )
        if not math.isclose(self.core_pct + self.satellite_pct, 100.0, abs_tol=1e-5):
            raise ValueError(
                f"core_pct({self.core_pct}) + satellite_pct({self.satellite_pct}) "
                f"= {self.core_pct + self.satellite_pct:.4f} ≠ 100. "
                "core_pct와 satellite_pct 합계를 100으로 맞추세요."
            )
        return self


# ─────────────────────────────────────────────
# stock-recommender 출력 스키마 (Step 4)
# ─────────────────────────────────────────────
class StockProduct(BaseModel):
    name: str = Field(..., description="상품명 또는 종목명")
    ticker: Optional[str] = Field(
        None, description="Yahoo Finance 티커. 파킹통장·예금 등은 null"
    )
    account_type: str = Field(
        ..., description="IRP / ISA / 연금저축 / 일반계좌 / 파킹통장"
    )
    monthly_amount: int = Field(..., ge=0, description="월 총 납입 예정액 (원)")
    lump_sum_amount: Optional[int] = Field(
        None, ge=0,
        description="목돈 일시 투자 배정액 (원). lump_sum_intent_krw가 있는 고객에게만 채움."
    )
    
    # ── [신규 추가] 자동 적립식 고도화 필드 ──
    investment_type: Literal["정수 단위 매수", "소수점 금액 지정 적립"] = Field(
        default="정수 단위 매수", description="자금 규모와 1주당 가격을 고려한 소수점 투자 필요 여부"
    )
    cycle_frequency: Literal["매일", "매주", "매월"] = Field(
        default="매월", description="추천하는 자동 적립 주기"
    )
    per_cycle_amount: Optional[int] = Field(
        None, ge=0,
        description=(
            "1회당 실제 자동 이체/매수 세팅 금액 (원). "
            "null이면 cycle_frequency 기반 자동 계산: 매월=monthly_amount, "
            "매주=monthly_amount÷4.3, 매일=monthly_amount÷21(영업일 기준). "
            "매주·매일은 증권사 앱 자동적립 설정 단위에 맞춰 1,000원 단위로 반올림."
        )
    )
    reason: Optional[str] = Field(
        None,
        description="추천 근거 및 구체적인 앱 자동 적립 설정 가이드 포함"
    )
    alternatives: Optional[dict] = Field(
        None,
        description=(
            "이 종목의 역할·성격 + 같은 성격의 대체 가능 종목 가이드 객체. "
            "키: role(🎯 이 자리의 역할), why_this(💡 왜 이 성격), "
            "swap_examples(🔁 같은 성격의 대체 가능 종목 2~3개), principle(핵심은 종목이 아니라 역할). "
            "투자자가 비슷한 성격의 다른 종목으로 융통성 있게 교체할 수 있도록 한다."
        )
    )

    @model_validator(mode='after')
    def auto_compute_per_cycle_amount(self) -> 'StockProduct':
        """per_cycle_amount 미입력 시 cycle_frequency 기반으로 자동 계산.

        stock-recommender.md 프롬프트와 동일 규칙: 매주=÷4.3, 매일=÷21(영업일).
        매주·매일은 증권사 앱 자동적립 설정 단위에 맞춰 1,000원 단위로 반올림한다.
        (LLM이 직접 계산한 값과 Pydantic fallback 값이 어긋나지 않도록 일치시킴)
        """
        if self.per_cycle_amount is None:
            divisors = {"매월": 1, "매주": 4.3, "매일": 21}
            div = divisors.get(self.cycle_frequency, 1)
            raw_amt = self.monthly_amount / div if div > 0 else self.monthly_amount
            if self.cycle_frequency in ("매주", "매일"):
                self.per_cycle_amount = int(round(raw_amt / 1000) * 1000)
            else:
                self.per_cycle_amount = int(round(raw_amt))
        return self


# ─────────────────────────────────────────────
# 정량적 성과 및 리스크 지표 (상관계수·리스크 스코어러 공용)
# ─────────────────────────────────────────────
class QuantitativeMetrics(BaseModel):
    sharpe_ratio: Optional[float] = Field(None, description="샤프 지수 (변동성 대비 초과 수익)")
    sortino_ratio: Optional[float] = Field(None, description="소르티노 비율 (하방 변동성 대비 초과 수익)")
    calmar_ratio: Optional[float] = Field(None, description="칼마 비율 (최대 낙폭 대비 수익)")
    beta: Optional[float] = Field(None, description="벤치마크 대비 민감도")
    turnover_rate: Optional[float] = Field(None, description="포트폴리오 자산 회전율")
    mdd: Optional[float] = Field(None, description="최대 낙폭 (Max Drawdown, 음수 소수 — risk-scorer 전용)")
    mdd_pct: Optional[float] = Field(None, description="최대 낙폭 % (calculate_advanced_metrics 출력 — correlation_analyzer 전용)")
    annualized_return_pct: Optional[float] = Field(None, description="연환산 수익률 (%)")


# ─────────────────────────────────────────────
# 상관계수 분석 출력 스키마 (Step 1.6)
# ─────────────────────────────────────────────
class CorrelationPair(BaseModel):
    asset_a: str = Field(..., description="자산 A 종목명")
    asset_b: str = Field(..., description="자산 B 종목명")
    correlation: float = Field(..., ge=-1.0, le=1.0, description="피어슨 상관계수")
    verdict: str = Field(..., description="진단 문구 (예: '매우 높은 상관관계 — 사실상 중복 투자')")


class CorrelationAnalysisOutput(BaseModel):
    client_id: str
    analyzed_at: str  # YYYY-MM-DD
    portfolio_diversification_score: int = Field(
        ..., ge=0, le=100,
        description="포트폴리오 분산 품질 점수 (100점 만점 — 높을수록 잘 분산됨)"
    )
    pseudo_diversification_detected: bool = Field(
        ..., description="r≥0.8 쌍 발견 여부 (가짜 분산 감지)"
    )
    high_correlation_pairs: List[CorrelationPair] = Field(
        default_factory=list,
        description="상관계수 0.8 이상인 자산 쌍 목록"
    )
    action_nudge: Optional[str] = Field(
        None, description="리포트 삽입용 처방 문구"
    )
    fallback_used: bool = Field(
        default=False,
        description="yfinance 실패로 섹터 기반 정적 행렬을 사용했는지 여부"
    )
    note: Optional[str] = Field(None, description="분석 과정 메모")
    # ── 신규: 정량 리스크 지표 (risk_calculator 연산 결과) ──
    portfolio_metrics: Optional[QuantitativeMetrics] = Field(
        None,
        description="포트폴리오 가중 수익률 기반 Sharpe/Sortino/MDD/Calmar/Beta (1년 백테스트)"
    )
    stress_test: Optional[Dict] = Field(
        None,
        description="역사적 스트레스 테스트 (2008/2020/2022 시나리오별 예상 손실)"
    )
    etf_lookthrough: Optional[Dict] = Field(
        None,
        description="ETF 룩스루 분석 결과 (실질 단일 종목 집중도 및 15% 초과 경고)"
    )


# (추가) 4단계: 기존 보유 종목 처방 스키마
class ExistingHoldingAction(BaseModel):
    name: str
    action: Literal["유지 및 관망", "추가 매수", "반등 시 분할 매도(Core 교체)", "청산 전 즉시 매도 검토"]
    reason: str
    account_location: Optional[str] = Field(
        None, description="현재 보유 계좌 (ISA / IRP / 연금저축 / 일반)"
    )
    core_or_satellite: Optional[Literal["Core", "Satellite"]] = Field(
        None, description="코어/위성 분류"
    )
    tax_action: Optional[str] = Field(
        None,
        description="세금 관련 액션 (예: 'Tax-Loss Harvesting 대상 — 손실 확정 후 유사 ETF 재매수')"
    )

class StockPlanOutput(BaseModel):
    client_id: str
    created_at: str  # YYYY-MM-DD
    # (추가) 2단계 유동성 잠금 방지 검증을 위해 kyc의 goal_type을 함께 받도록 추가
    gbi_goal_type: GoalType = Field(default=GoalType.UNSET, description="KYC에서 전달받은 고객 목표 타입")
    safe_products: List[StockProduct] = Field(..., description="안전 버킷 상품 목록 (파킹통장·단기채 등)")
    core_products: List[StockProduct] = Field(..., description="성장 Core 상품 목록 (지수추종 ETF 등)")
    satellite_products: List[StockProduct] = Field(..., description="성장 Satellite 상품 목록 (개별주·테마ETF 등)")
    # (추가) 4단계 기존 종목 가이드
    existing_holdings_guide: List[ExistingHoldingAction] = Field(
        default_factory=list,
        description="고객이 기존에 보유 중인 종목들에 대한 액션 플랜"
    )
    year_end_tax_schedule: Optional[Dict] = Field(
        None,
        description="연말 절세 매매 스케줄 (T+2 기준 데드라인, 복수 증권사 합산 주의사항 등)"
    )
    total_monthly: int = Field(..., ge=0, description="월 총 투자 금액 (원)")
    total_lump_sum: Optional[int] = Field(
        None, ge=0,
        description="목돈 일시 투자 총액 (원). lump_sum_amount 합산과 일치해야 함."
    )

    # ── 리포트 렌더링용 텍스트 필드 (report-writer 플레이스홀더 치환 대상) ──
    stock_plan_md: Optional[str] = Field(
        None, description="report-writer {{STOCK_PLAN}} 치환용 마크다운 텍스트"
    )
    stock_plan_html: Optional[str] = Field(
        None, description="report-writer {{STOCK_PLAN_HTML}} 치환용 HTML 텍스트"
    )
    monthly_plan: Optional[str] = Field(
        None, description="report-writer {{MONTHLY_PLAN}} 치환용 월 분할 매수 플랜 텍스트"
    )
    execution_guide: Optional[str] = Field(
        None, description="report-writer {{EXECUTION_GUIDE}} 치환용 증권사 앱 실행 가이드"
    )

    @model_validator(mode='after')
    def check_monthly_total(self) -> 'StockPlanOutput':
        computed = (
            sum(p.monthly_amount for p in self.safe_products)
            + sum(p.monthly_amount for p in self.core_products)
            + sum(p.monthly_amount for p in self.satellite_products)
        )
        tolerance = max(TOLERANCE_MIN_KRW, int(self.total_monthly * TOLERANCE_RATE))
        if abs(computed - self.total_monthly) > tolerance:
            raise ValueError(
                f"total_monthly({self.total_monthly:,}) ≠ 상품별 합계({computed:,}). "
                f"허용 오차({tolerance:,}원) 초과 — 각 상품의 monthly_amount 합산과 total_monthly를 일치시키세요."
            )
        return self

    @model_validator(mode='after')
    def check_lump_sum_total(self) -> 'StockPlanOutput':
        if self.total_lump_sum is None:
            return self
        all_products = self.safe_products + self.core_products + self.satellite_products
        computed = sum((p.lump_sum_amount or 0) for p in all_products)
        tolerance = max(TOLERANCE_MIN_KRW, int(self.total_lump_sum * TOLERANCE_RATE))
        if abs(computed - self.total_lump_sum) > tolerance:
            raise ValueError(
                f"total_lump_sum({self.total_lump_sum:,}) ≠ 상품별 lump_sum_amount 합계({computed:,}). "
                f"허용 오차({tolerance:,}원) 초과 — 각 상품의 lump_sum_amount 합산과 total_lump_sum을 일치시키세요."
            )
        return self

    # (추가) 2단계: 유동성 잠금 방지 강력 통제 로직
    @model_validator(mode='after')
    def check_liquidity_lock(self) -> 'StockPlanOutput':
        all_products = self.safe_products + self.core_products + self.satellite_products
        if self.gbi_goal_type in (GoalType.HOUSING, GoalType.SHORT_TERM):
            # IRP·연금저축: housing/short_term 모두 차단 (frozenset 정확 매칭)
            locked_accounts = [
                p.name for p in all_products
                if p.account_type.strip() in _LOCKED_ACCOUNT_TYPES
            ]
            if locked_accounts:
                raise ValueError(
                    f"단기/중기 자금 목표(housing/short_term) 고객에게 유동성이 묶이는 연금저축/IRP 계좌({locked_accounts})를 추천할 수 없습니다. "
                    "ISA 또는 일반계좌로 변경하세요."
                )
        if self.gbi_goal_type == GoalType.SHORT_TERM:
            # ISA(3년 락업): short_term 목표에는 부적합 (housing은 3~5년일 수 있어 허용)
            isa_locked = [
                p.name for p in all_products
                if p.account_type.strip() in _ISA_ACCOUNT_TYPES
            ]
            if isa_locked:
                raise ValueError(
                    f"단기 목표(short_term) 고객에게 3년 락업 ISA 계좌({isa_locked})를 추천할 수 없습니다. "
                    "일반계좌로 변경하세요."
                )
        return self


# ─────────────────────────────────────────────
# risk-scorer 에이전트의 출력 스키마 (Step 5)
# ─────────────────────────────────────────────
class RiskScorerOutput(BaseModel):
    total_score: int = Field(..., ge=0, le=100,
                             description="0~100 사이의 종합 재무 건강 점수")
    grade: str = Field(..., pattern="^[🟢🟡🔴]$",
                       description="상태를 나타내는 신호등 이모지")
    grade_message: Optional[str] = Field(
        None, description="등급 설명 문구 — report-writer {{GRADE_MESSAGE}} 치환용"
    )
    details: Optional[Dict[str, dict]] = Field(
        None,
        description="지표별 상세 점수 {지표명: {score, max, comment}}"
    )
    penalty_score: int = Field(
        default=0, le=0,
        description=(
            "특수 페널티 합계 (0 또는 음수). '4지표 합산 총점에서 차감'하는 페널티만 "
            "여기에 기록한다. 예: 고금리 악성 부채 보유 -15점. "
            "행동갭·가짜분산 차감은 details 내부 score에 이미 반영되므로 여기 넣지 않는다."
        )
    )
    penalty_reason: Optional[str] = Field(
        None,
        description="penalty_score 적용 사유 (예: '고금리 악성 부채 보유 -15점')"
    )
    urgent_actions: List[str] = Field(
        ..., min_length=1,
        description="즉시 실행해야 할 행동 과제 목록"
    )
    fact_bomb: str = Field(..., description="팩트 폭격 코멘트")
    quantitative_metrics: Optional[QuantitativeMetrics] = Field(
        None,
        description="상담 사후 평가용 정량 리스크 지표"
    )
    recommended_actions: Optional[Dict] = Field(
        None,
        description=(
            "구조화된 추천 액션 — 다음 재진단 시 이행 여부 추적용. "
            "{'sell': ['ticker_or_name', ...], 'reduce': [...], 'hold': [...], 'pay_off_debt': bool}"
        )
    )

    @model_validator(mode='after')
    def check_score_consistency(self) -> 'RiskScorerOutput':
        """4지표 score 합 + penalty_score 를 0~100으로 clamp 한 값이 total_score 와
        정확히 일치하는지 강제한다.

        - 표준 4지표(cashflow·behavioral_gap·emergency_fund·diversification) 구조이고
          각 score 가 숫자일 때만 검증한다. (LLM 이 비표준 details 를 낼 경우 관대 스킵)
        - clamp 처리: risk-scorer.md 의 '최하 점수 0점' 규칙과 일관되도록
          max(0, min(100, 합)) 으로 비교한다.
        """
        if not self.details:
            return self
        keys = ["cashflow", "behavioral_gap", "emergency_fund", "diversification"]
        subscores = []
        for k in keys:
            d = self.details.get(k)
            if not isinstance(d, dict) or not isinstance(d.get("score"), (int, float)):
                return self  # 표준 4지표 구조가 아니면 합계 검증 스킵
            subscores.append(d["score"])
        raw = sum(subscores) + self.penalty_score
        expected = max(0, min(100, int(raw)))
        if expected != self.total_score:
            raise ValueError(
                f"점수 합계 불일치: 4지표 합({int(sum(subscores))}) + penalty_score({self.penalty_score}) "
                f"= {int(raw)} → clamp(0~100) {expected} ≠ total_score({self.total_score}). "
                "4지표(cashflow·behavioral_gap·emergency_fund·diversification)의 score 합에 "
                "penalty_score를 더하고 0~100으로 clamp한 값이 total_score와 정확히 일치해야 합니다. "
                "고금리 부채 등 '총점에서 차감'하는 페널티는 details가 아니라 penalty_score 필드에 "
                "음수로 기록하세요."
            )
        return self


# ─────────────────────────────────────────────
# reviewer 출력 스키마 (Step 7)
# ─────────────────────────────────────────────
class ReviewerOutput(BaseModel):
    client_id: str
    reviewed_at: str
    verdict: Literal["PASS", "PASS WITH WARNING", "FAIL"]
    checks: Dict[str, str]
    warnings: List[str]
    report_confirmed: bool

    @model_validator(mode='after')
    def check_confirmed_consistency(self) -> 'ReviewerOutput':
        if self.verdict == "FAIL" and self.report_confirmed:
            raise ValueError("verdict가 FAIL인데 report_confirmed=True는 불가합니다.")
        if self.verdict in ("PASS", "PASS WITH WARNING") and not self.report_confirmed:
            raise ValueError("verdict가 PASS인데 report_confirmed=False는 불가합니다.")
        return self
