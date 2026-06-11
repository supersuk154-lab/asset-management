# 파이프라인 이슈 로그 — 2026-06-04

> 테스트 대상: client_20260604_001 (30대 직장인 / 중립형), client_20260604_002 (50대 자영업자 / 안정형)
> 작성 기준: 오늘 실제 실행 중 발생한 오류·경고·수동 개입 사례만 기록

---

## ISSUE-01 · kyc-collector가 파일 저장 없이 "완료" 보고

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 1 — client_20260604_002 kyc-collector |
| **증상** | 에이전트가 "파일이 정상적으로 저장되었습니다"라고 보고했으나 Write 툴을 실제 호출하지 않아 `kyc.json` 미생성 |
| **발견 방법** | `validate --agent kyc` → `❌ 파일 없음` 오류 |
| **해결** | 재호출 시 프롬프트에 "반드시 Write 툴을 호출하여 파일을 생성해야 한다" 명시 → 정상 저장 |
| **재현 조건** | client_id가 새 폴더일 때, 에이전트가 결과를 텍스트로만 출력하고 툴 호출 생략 |

**개선 방안:**
- `kyc-collector.md` 상단에 `## 필수: Write 툴 호출 의무` 섹션 추가
- `orchestrator.py prepare` 출력의 Step 1 커맨드 안내에 "저장 여부를 validate로 반드시 확인" 경고 추가
- 향후 Phase 5-C 자동화 시 `call_agent_with_validation`에서 파일 존재 여부를 validate 전에 먼저 체크하는 로직 추가

---

## ISSUE-02 · portfolio-designer가 rebalancing_rule을 객체로 저장

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 3 → validate — client_20260604_002 portfolio.json |
| **증상** | `rebalancing_rule` 필드가 `string` 타입이어야 하는데 에이전트가 `{frequency, method, tolerance_bands, ...}` 중첩 객체로 저장 |
| **발견 방법** | `validate --agent portfolio` → `❌ Input should be a valid string` |
| **해결** | 오케스트레이터(Claude)가 JSON 직접 편집 — 객체 내용을 평문 문자열로 플래튼 |
| **재현 조건** | 상관관계 분석 결과(고상관 쌍)가 있을 때 에이전트가 복잡한 허용 밴드 정보를 구조화하려는 경향 |

**개선 방안:**
- `schemas.py` `PortfolioDesignerOutput`의 `rebalancing_rule` 필드 주석에 `# 반드시 plain string — 객체/배열 금지` 명시
- `portfolio-designer.md`에 예시 추가:
  ```
  ✅ "rebalancing_rule": "연 1회. Watering 우선. 허용 밴드 ±8%..."
  ❌ "rebalancing_rule": {"frequency": "연 1회", "method": "..."}
  ```
- `validate --agent portfolio` FAIL 시 자가 수정 루프에서 해당 필드만 재작성 요청하도록 오류 메시지 개선

---

## ISSUE-03 · report-writer HTML 디자인 회귀 (CSS 클래스 미사용)

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 6 — client_20260531_001 2026-06-03 리포트 |
| **증상** | v3 버전(`2026-06-02_v3.html`)에서 잘 작동하던 CSS 클래스들이 미적용. `warn-box`, `info-box`, `nudge-box`, `bias-item`, `bias-title`, `urgent-list`, `corr-table`, `action-banner`, `action-table`, `checklist-section` 등이 인라인 스타일로 대체되어 색상 구분 소멸 |
| **발견 방법** | 사용자가 "보라색·파란색·빨간색·녹색 강조 글씨가 없어진 것 같다"고 지적 |
| **해결** | HTML 전면 재작성: 구조 재배치 + CSS 클래스 전면 재적용 + 종목명 색상 배지 추가 + 용어 툴팁 추가 |

**개선 방안:**
- `report-writer.md`에 **CSS 클래스 의무 사용 목록** 추가:
  ```
  warn-box     → 경고 (빨간)
  info-box     → 정보 (파란)
  nudge-box    → 넛지 (노란)
  success-box  → 긍정 (초록)
  bias-item + bias-title → 행동재무 (보라)
  urgent-list  → 즉시 실행 (빨간 번호)
  action-banner → 최상단 행동 지침 (파란)
  corr-table + r-critical/r-high → 상관계수 테이블
  action-table → 실행 요약 (보라 헤더)
  stk.sell/safe/core/sat → 종목명 색상 배지
  term[data-tip] → 용어 툴팁
  ```
- `validate --agent report` 단계에서 플레이스홀더 체크 외에 핵심 CSS 클래스 존재 여부도 grep으로 검사하는 로직 추가 검토

---

## ISSUE-04 · report-writer 용어 툴팁(.term) 미적용

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 6 — client_20260531_001 2026-06-03 리포트 |
| **증상** | CSS에 `.term { border-bottom: 1px dashed ... }` 클래스가 정의되어 있었으나 HTML 본문에서 한 번도 사용되지 않음 |
| **발견 방법** | 사용자 요청으로 확인 |
| **해결** | IRP, 연금저축, ETF, CMA, 세액공제, 배당, 리밸런싱, 양도소득세 등 핵심 용어 8종에 `<span class="term" data-tip="...">` 수동 적용 |

**개선 방안:**
- `report-writer.md`에 **표준 용어 툴팁 사전** 추가 (용어 → data-tip 내용 대응표)
- 주요 용어 첫 등장 시 반드시 `.term` 클래스 적용 규칙 명시
- `master_report.html` 템플릿에 용어 툴팁 예시 코드 삽입

---

## ISSUE-05 · 비상예비비 기준 에이전트 간 불일치

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 3(portfolio) vs Step 5(risk-scorer) — client_20260604_001 |
| **증상** | `risk-scorer`는 급여소득자 3개월 기준(1,050만원)을 사용, `portfolio-designer`와 리포트는 6개월 기준(2,100만원)을 사용. reviewer가 V20 경고 발동 |
| **판정** | "더 보수적인 방향이므로 재작업 불필요" — PASS WITH WARNING |
| **영향** | 점수·리포트 수치가 에이전트마다 다른 기준을 참조할 경우 일관성 훼손 |

**개선 방안:**
- `compliance_rules.json`에 직업유형별 비상예비비 기준 명시:
  ```json
  "emergency_fund_standards": {
    "급여소득자": "3~6개월 (권장 6개월)",
    "자영업/프리랜서": "6개월 필수"
  }
  ```
- `schemas.py` 또는 `CLAUDE.md`에 표준 기준 통일 명시 → 모든 에이전트가 동일 기준 참조

---

## ISSUE-06 · 보고서 내 전문 용어 난이도 (구조적 문제)

| 항목 | 내용 |
|------|------|
| **발생 단계** | Step 6 — client_20260531_001 2026-06-03 리포트 (사후 개선) |
| **증상** | "TDF 글라이드 패스 공식: max(30, 90-1.6×(45-35))=74%", "ETF 룩스루", "Tax-Loss Harvesting", "risk_conflict", "GBI 버킷", "젬(Gem)의 팩트 폭격" 등 전문 용어가 주식 비전문가 고객에게 그대로 노출 |
| **발견 방법** | 사용자가 "주식 모르는 고객이 보기 어렵다"고 지적 |
| **해결** | 수식을 `<details>` 접이식으로 숨김, 섹션 제목 쉽게 변경, 용어 한글화 |

**개선 방안:**
- `report-writer.md`에 **용어 치환 규칙표** 추가:

  | 원문 | 쉬운 표현 |
  |------|----------|
  | ETF 룩스루 | ETF 속 실제 비중 합산 계산 |
  | Tax-Loss Harvesting | 손실 절세 매도 전략 |
  | risk_conflict | 성향-포트폴리오 불일치 |
  | GBI 버킷 | 목적별 자금 바구니 |
  | Core/Satellite | 주력/보조 자산 |
  | 글라이드 패스 | 나이에 따른 위험 비중 조정 원칙 |
  | 젬(Gem)의 팩트 폭격 | 핵심 진단 |

- 계산 수식은 본문에서 제거하고 `<details>` 접이식 안에만 표시 규칙 추가

---

## 요약

| # | 이슈 | 심각도 | 수동 개입 필요 여부 | 우선순위 |
|---|------|--------|-------------------|---------|
| 01 | kyc-collector 파일 미저장 | 🔴 높음 | Yes — 재실행 필요 | 즉시 |
| 02 | rebalancing_rule 타입 오류 | 🟡 중간 | Yes — JSON 직접 수정 | 즉시 |
| 03 | HTML 디자인 CSS 클래스 회귀 | 🟡 중간 | Yes — 전면 재작성 | 즉시 |
| 04 | 용어 툴팁 미적용 | 🟢 낮음 | Yes — 수동 추가 | 다음 스프린트 |
| 05 | 비상예비비 기준 불일치 | 🟡 중간 | No — PASS WITH WARNING | 다음 스프린트 |
| 06 | 보고서 전문 용어 난이도 | 🟢 낮음 | Yes — 전면 재작성 | 다음 스프린트 |

**즉시 반영 권고 파일:**
- `.claude/agents/kyc-collector.md` — Write 툴 호출 의무화
- `.claude/agents/portfolio-designer.md` — rebalancing_rule 타입 예시
- `.claude/agents/report-writer.md` — CSS 클래스 목록, 용어 치환표, 툴팁 사전
- `scripts/schemas.py` — rebalancing_rule 주석 강화, 비상예비비 기준 주석
- `scripts/compliance_rules.json` — emergency_fund_standards 섹션 추가
