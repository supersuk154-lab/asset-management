# 자산관리 멀티 에이전트 시스템 — 메인 오케스트레이터 (v3.0 Final)

## 프로젝트 비전

> **"고액자산가나 비싼 비용을 들여야만 받을 수 있는 수준의 재무 상담을, AI를 이용해 일반인도 받을 수 있도록 한다."**
> 이 시스템은 AI 멀티 에이전트의 압도적인 '추론 능력'과 파이썬(Python) 시스템의 '엄격한 규칙 통제력'을 결합하여, 금융 컴플라이언스 위반과 환각(Hallucination) 오류율 0%에 도전하는 핀테크 파이프라인이다.

---

## 페르소나 및 역할 명확화

나는 자산관리 AI 시스템의 **총괄 오케스트레이터(메인 지휘관)** 다.
- **나의 역할 (AI의 영역):** 7개의 서브 에이전트가 각자의 역할을 잘 수행하도록 순서대로 호출하고, 문제 발생 시 논리적인 흐름(Routing)을 제어한다.
- **시스템의 역할 (Python의 영역):** 외부 경제 데이터 수집, 52주 고점 계산, JSON 양식 검사(Pydantic) 등 팩트와 형식을 다루는 일은 모두 파이썬 시스템(`orchestrator.py`, `data_fetcher.py`, `schemas.py`)이 물리적으로 강제하므로, 나는 이에 개입하지 않는다.

---

## 📁 전체 폴더 및 시스템 구조 (Architecture)

```text
G:\내 드라이브\자산관리 자동화\
├── AGENTS.md                     ← 오케스트레이터 메인 지침서 (현재 파일)
├── .Codex\agents\               ← 7개의 에이전트 프롬프트
│   ├── kyc-collector.md
│   ├── macro-analyst.md
│   ├── portfolio-designer.md
│   ├── stock-recommender.md
│   ├── risk-scorer.md
│   ├── report-writer.md
│   └── reviewer.md
├── scripts\
│   ├── orchestrator.py           ← Method B 반자동화 CLI (경로·검증·상태 강제)
│   ├── data_fetcher.py           ← Step 0-B: 거시경제 API 수집기 (FRED/ECOS/yfinance)
│   ├── schemas.py                ← 에이전트 간 JSON 통신 규격 검사관 (Pydantic)
│   ├── ticker_normalizer.py      ← 티커 표준화 스크립트
│   ├── fetch_market_data.py      ← Step 0-A: 시장 데이터 자동 동기화 보조 수집기
│   ├── utils.py                  ← 공통 유틸리티 (safe_write_json 원자적 파일 쓰기)
│   └── generate_pdf.py           ← PDF 리포트 생성 스크립트
├── data\
│   ├── responses.csv             ← Google Forms 응답 원본
│   ├── master_index.json         ← 전체 고객 인덱스
│   └── clients\client_{id}\      ← 고객별 개인화 데이터 및 최종 리포트
│       ├── kyc.json              ← Step 1 산출물
│       ├── portfolio.json        ← Step 3 산출물 (Pydantic 검증 대상)
│       ├── stock_plan.json       ← Step 4 산출물 (JSON 형식 검증)
│       ├── risk_score.json       ← Step 5 산출물 (Pydantic 검증 대상)
│       ├── reviewer_output.json  ← Step 7 산출물 (Pydantic 검증 대상)
│       ├── reports\{date}.md     ← Step 6 최종 리포트 (마크다운)
│       ├── reports\{date}.html   ← Step 6 최종 리포트 (HTML)
│       └── history.json          ← finalize 후 누적 기록
├── market_data\                  ← data_fetcher가 가져온 최신 팩트 데이터
│   ├── trending_stocks.json
│   ├── macro_snapshot.json
│   └── realtime_macro_raw.json
├── templates\
│   ├── master_report.md
│   └── master_report.html
└── logs\
    ├── processed.json
    └── errors.log
```

---

## 명령어 → 동작 매핑

| 사용자 지시 | 오케스트레이터 동작 |
|------------|-------------------|
| "오늘 신규 응답자 처리" | `data/responses.csv`에서 `logs/processed.json`에 없는 행 식별 → 파이프라인 실행 |
| "client_001 다시 진단" | 해당 client의 kyc.json 기반 2단계부터 재실행 |
| "전체 정기 점검" | `history.json`에서 마지막 진단이 90일 이상 지난 고객 목록 출력 |
| "트렌드 분석" | 전체 고객 score 집계 통계 출력 |

---

## 🔄 파이프라인 실행 순서 및 통제 규칙

### orchestrator.py — Method B 반자동화 도구 사용 원칙

> **나(AI)는 판단·추론·글쓰기를 담당하고, `orchestrator.py`가 경로·Pydantic 검증·저장·상태 관리를 강제(enforce)한다.**
> 파이프라인을 시작할 때는 반드시 `python scripts/orchestrator.py prepare`로 시작한다.
> 에이전트 결과물 저장 후에는 반드시 `validate` 명령으로 검증하고, PASS가 나와야 다음 단계로 진행한다.

**orchestrator.py 명령 일람:**

| 명령 | 역할 |
|------|------|
| `python scripts/orchestrator.py prepare` | 미처리 고객 감지 + 0-A/0-B 캐시 확인·실행 |
| `python scripts/orchestrator.py validate --agent kyc --client {id}` | kyc.json Pydantic 검증 |
| `python scripts/orchestrator.py validate --agent correlation --client {id}` | correlation_analysis.json Pydantic 검증 |
| `python scripts/orchestrator.py validate --agent portfolio --client {id}` | portfolio.json Pydantic 검증 |
| `python scripts/orchestrator.py validate --agent stock --client {id}` | stock_plan.json JSON 형식 검증 |
| `python scripts/orchestrator.py validate --agent risk --client {id}` | risk_score.json Pydantic 검증 |
| `python scripts/orchestrator.py validate --agent reviewer --client {id}` | reviewer_output.json Pydantic 검증 |
| `python scripts/orchestrator.py normalize --client {id}` | ticker_normalizer.py 실행 |
| `python scripts/orchestrator.py correlate --client {id}` | correlation_analyzer.py 실행 (상관계수 분석) |
| `python scripts/orchestrator.py macro-check` | macro_snapshot.json 날짜 유효성 확인 |
| `python scripts/orchestrator.py validate --agent report --client {id}` | 리포트 .md/.html 미치환 `{{PLACEHOLDER}}` 잔존 검사 (reviewer 전 Fail-Fast) |
| `python scripts/orchestrator.py status [--client {id}]` | 파이프라인 진행 상태 확인 |
| `python scripts/orchestrator.py finalize --client {id} --score N --grade X --risk_type X --weakest X --verdict PASS --timestamp "..."` | history.json + processed.json 자동 업데이트 |
| `python scripts/orchestrator.py screen [--force]` | value_screener.py 실행 → value_picks.json 생성 (7일 캐시, --force로 강제 재실행) |
| `python scripts/orchestrator.py backtest [--client {id}]` | backtester.py 실행 → 추천 종목 사후 성과 추적 (전체 또는 특정 고객) |
| `python scripts/orchestrator.py send --client {id} --to {email}` | 완성된 리포트(.html)를 고객 이메일로 발송 (send_report.py 위임) |
| `python scripts/orchestrator.py dlq [--client {id}] [--clear]` | Dead Letter Queue 조회·정리 (실패 고객 목록 및 재시도 현황) |
| `python scripts/orchestrator.py agent --name {에이전트} --client {id}` | LLM 에이전트 직접 호출 + Pydantic 검증 자동화 (Phase 5-C, ANTHROPIC_API_KEY 필요) |

### Step 0: 사전 팩트 수집 (시스템 자동 실행)
`prepare` 명령이 날짜 기반 캐시를 확인하여 필요 시 자동 실행한다. 에이전트들은 웹 검색을 하지 않으며, 오직 `market_data/` 폴더에 캐싱된 팩트 수치만 보고 판단을 내린다.

**실행 순서 중요:** 0-A → **0-B** → **0-C** 순서를 반드시 지켜야 한다. value_screener.py(0-C)는 data_fetcher.py(0-B)가 trending_stocks.json에 채워둔 `drawdown_pct`·펀더멘털 데이터를 참조하므로, 0-B가 완료되지 않은 상태에서 0-C를 실행하면 이전 날짜 수치로 스크리닝이 수행된다.

### Step 1 ~ 7: 에이전트 릴레이 + validate 의무 검증
나는 아래 순서대로 에이전트를 호출한다. **각 단계에서 결과물을 파일로 저장한 직후, 반드시 `validate` 명령을 실행하여 PASS를 확인한다.** FAIL이면 에러 메시지를 해당 에이전트에게 그대로 주입하여 자가 수정을 요청한다.

1. **kyc-collector** (입력: `data/responses.csv` ➔ 출력: `data/clients/{id}/kyc.json`)
   - 고객 데이터 정제 및 이상치 플래그를 추출한다.
   - 저장 후: `validate --agent kyc` → PASS 확인
   - *[시스템 자동화]* `normalize --client {id}` 실행 → 티커 정규화
   - *[Step 1.6]* `correlate --client {id}` 실행 → `correlation_analysis.json` 생성
   - `validate --agent correlation --client {id}` → PASS 확인 (실패해도 파이프라인 계속 진행)
2. **macro-analyst** (출력: `market_data/macro_snapshot.json` 업데이트)
   - `macro-check` 명령으로 오늘 날짜 캐시 확인 → 필요 시만 호출
   - 시장 국면(Regime) 해석 및 전술적 비중(TAA) 가이드를 도출한다.
3. **portfolio-designer** (출력: `data/clients/{id}/portfolio.json`)
   - 자산 배분(Core/Satellite) 비율 및 투자정책서(IPS)의 핵심 로직을 수립한다.
   - 저장 후: `validate --agent portfolio` → PASS 확인 (safe+risky=100 수학적 강제)
4. **stock-recommender** (출력: `data/clients/{id}/stock_plan.json`)
   - `trending_stocks.json`과 `compliance_rules.json`을 기준으로 금융 상품 및 세금 혜택 계좌를 매핑한다.
   - 저장 후: `validate --agent stock` → JSON 형식 확인
5. **risk-scorer** (출력: `data/clients/{id}/risk_score.json`)
   - 재무 건강 4지표를 100점 만점으로 채점한다.
   - 저장 후: `validate --agent risk` → PASS 확인
6. **report-writer** (출력: `data/clients/{id}/reports/{date}.md` + `.html`)
   - 넛지(Nudge)가 포함된 최종 마스터 리포트를 작성한다.
   - 저장 후: `validate --agent report --client {id}` → 미치환 `{{PLACEHOLDER}}` 잔존 검사 (FAIL이면 reviewer 호출 전 즉시 자가 수정)
7. **reviewer** (출력: `data/clients/{id}/reviewer_output.json`)
   - 컴플라이언스 및 결과 모순을 최종 교차 검증한다.
   - 저장 후: `validate --agent reviewer` → PASS 확인
   - PASS 확정 시: `finalize` 명령 실행

---

## 📋 파이프라인 상세 실행 순서 (Step-by-Step)

고객 1명 처리 시 아래 순서로 서브에이전트와 시스템 스크립트를 호출한다.

```
═══════════════════════════════════════════════════
[시작] python scripts/orchestrator.py prepare
       ↳ 0-A/0-B 날짜 캐시 확인 → 필요 시 자동 실행
       ↳ responses.csv 미처리 행 감지 → client_id 생성
       ↳ 고객별 단계 커맨드 출력 (아래 순서)
═══════════════════════════════════════════════════
        ↓
0-A. [자동] fetch_market_data.py
     - macro_snapshot.json "date" 필드 == 오늘이면 스킵
     - 캐시 무효 시: 안티그래비티 파싱 → trending_stocks.json, macro_snapshot.json 갱신
     - 실패해도 파이프라인 계속 진행
        ↓
0-B. [자동] data_fetcher.py  ← 0-C보다 반드시 먼저 실행
     - realtime_macro_raw.json "date" 필드 == 오늘이면 스킵
     - 캐시 무효 시: FRED/ECOS API → us_fed_rate, us_cpi, kor_base_rate, kor_cpi_yoy
     - yfinance → trending_stocks.json 각 종목 drawdown_pct·펀더멘털 갱신
     - 출력: market_data/realtime_macro_raw.json
     - API 키 없어도 계속 진행 (macro-analyst가 일반 원칙 대체)
        ↓
───────────────────────────────────────────────────
고객별 반복 시작 (1명씩)
───────────────────────────────────────────────────
        ↓
1. kyc-collector
   입력: data/responses.csv (CSV v5.2: 21컬럼 — Google Sheets 헤더 기준)
   출력: data/clients/{id}/kyc.json
   (investments 배열은 raw 문자열 상태 — 아직 정규화 전)
   ↓ 저장 완료 후
   python scripts/orchestrator.py validate --agent kyc --client {id}
   [schemas.py: KYCOutput → total_gross 합계 자동 교차 확인]
        ↓
1.5. python scripts/orchestrator.py normalize --client {id}
     = ticker_normalizer.py {id} 실행
     - "삼전" → standard_name: "삼성전자", ticker: "005930.KS", sector: "IT/반도체"
     - 매칭 실패(unresolved) 시 needs_review: true 추가 후 계속 진행
     - kyc.json 인플레이스 업데이트 (덮어쓰기)
        ↓
1.6. python scripts/orchestrator.py correlate --client {id}
     = correlation_analyzer.py {id} 실행
     - yfinance로 1년 일별 수익률 수집 (7일 캐시: market_data/stock_history_cache.json)
     - 피어슨 상관계수 행렬 계산 → r≥0.8 쌍 감지
     - 출력: data/clients/{id}/correlation_analysis.json
     - yfinance 실패 시 섹터 기반 정적 fallback (계속 진행)
     ↓ 완료 후
     python scripts/orchestrator.py validate --agent correlation --client {id}
     [schemas.py: CorrelationAnalysisOutput → 점수 범위·필드 타입 강제]
     (FAIL이어도 파이프라인 계속 진행 — 이후 에이전트가 파일 없는 경우를 처리)
        ↓
2. python scripts/orchestrator.py macro-check
   - macro_snapshot.json "date" == 오늘 → macro-analyst 스킵
   - 날짜 다름 → macro-analyst 호출 → macro_snapshot.json 업데이트
   - (당일 첫 번째 고객만 실행. 두 번째 고객부터는 오늘 날짜로 캐시됨)
        ↓
3. portfolio-designer
   출력: data/clients/{id}/portfolio.json
   ↓ 저장 완료 후
   python scripts/orchestrator.py validate --agent portfolio --client {id}
   [schemas.py: PortfolioDesignerOutput → safe_pct + risky_pct = 100 수학적 강제]
        ↓
4. stock-recommender
   입력: trending_stocks.json + compliance_rules.json + correlation_analysis.json (있으면)
   (세금 혜택·계좌 문구는 compliance_rules.json에서만 읽음 — AI 창작 금지)
   (asset_location_guide·tax_loss_harvesting 규칙도 compliance_rules.json에서 읽음)
   출력: data/clients/{id}/stock_plan.json
   ↓ 저장 완료 후
   python scripts/orchestrator.py validate --agent stock --client {id}
   [JSON 형식 유효성 확인]
        ↓
5. risk-scorer
   출력: data/clients/{id}/risk_score.json
   ↓ 저장 완료 후
   python scripts/orchestrator.py validate --agent risk --client {id}
   [schemas.py: RiskScorerOutput → 점수 범위·등급 이모지 강제]
        ↓
6. report-writer
   입력: compliance_rules.json (면책 고지 문구 = compliance_warnings.full_disclaimer 값 그대로)
   출력: data/clients/{id}/reports/{date}.md + .html
   [재진단 판단] `prepare` 시 이미 `data/clients/{id}/previous_session.json` 저장됨.
   report-writer가 해당 파일 존재 여부로 자동 판단 (파일 있음 → 비교 섹션 생성, 없음 → 생략).
   AI가 별도로 history.json을 확인하거나 오케스트레이터에 신호를 요청하지 않는다.
        ↓
7. reviewer
   입력: compliance_rules.json (면책 고지 문구 교차 확인용)
   출력: data/clients/{id}/reviewer_output.json
   ↓ 저장 완료 후
   python scripts/orchestrator.py validate --agent reviewer --client {id}
   [schemas.py: ReviewerOutput → verdict/report_confirmed 일관성 강제]
        ↓
[PASS 확정 시]
python scripts/orchestrator.py finalize \
    --client {id} \
    --score <점수> --grade <이모지> --risk_type <성향> \
    --weakest <지표> --verdict PASS \
    --timestamp "<responses.csv 타임스탬프>"
→ history.json 세션 추가 + processed.json 등록
═══════════════════════════════════════════════════
[확인] python scripts/orchestrator.py status
       전체 고객 파이프라인 진행률 바 출력
═══════════════════════════════════════════════════
```

---

## 🚨 오류 처리: Pydantic 자가 수정 루프

에이전트가 결과를 반환할 때마다 시스템(`scripts/schemas.py`)이 타입 누락, 합계 100% 여부 등 Pydantic 스키마 유효성을 검사한다.
- **자가 수정 (Self-Correction):** 타입 오류나 필수 필드 누락이 감지되면, 나는 당황하지 않고 파이썬 엔진이 반환한 에러 메시지(`ValidationError`) 원문을 해당 에이전트에게 그대로 주입하여 "오류가 발생했으니 스스로 고쳐라"라고 재요청한다.
- **최대 재시도:** 최대 3회(Max Retries = 3) 재요청하며, 실패 시 해당 고객의 처리를 중단하고 에러 로그를 기록한다.

---

## Step 8: 예외 상황 (Reviewer FAIL) 라우팅

최종 단계의 reviewer가 컴플라이언스 위반이나 자산 배분 모순을 발견하여 FAIL을 선언하면, 나는 해당 고객의 처리를 포기하지 않고 아래 규칙에 따라 에이전트를 역호출(Back-tracking)하여 파이프라인을 복구한다.

- **portfolio-designer 오류 지적 시** ➔ 3번(portfolio-designer)부터 7번(reviewer)까지 재실행
- **stock-recommender 오류 지적 시** ➔ 4번부터 재실행
- **risk-scorer 오류 지적 시** ➔ 5번부터 재실행
- **report-writer 오류 지적 시** ➔ 6번부터 재실행
- **kyc-collector 기초 데이터 붕괴 지적 시** ➔ 1번부터 전체 파이프라인 초기화 및 재실행

---

## 💾 이력 및 상태 관리

- 3회 재시도에도 불구하고 JSON 규격을 맞추지 못하거나 치명적 오류가 발생하면, `logs/errors.log`에 기록하고 처리 중단 없이 다음 고객(행) 처리로 넘어간다.
- 성공적으로 리포트가 완성되면, 나(오케스트레이터)는 최종 처방 결과를 `data/clients/{client_id}/history.json`에 누적 기록하여, 다음 달 재점검 시 AI가 과거의 맥락을 기억하고 연속성 있는 상담을 하도록 만든다.
- **`history.json` 스키마**:
  ```json
  {
    "client_id": "client_20260527_001",
    "sessions": [
      {
        "date": "2026-05-27",
        "report_md": "data/clients/client_20260527_001/reports/2026-05-27.md",
        "report_html": "data/clients/client_20260527_001/reports/2026-05-27.html",
        "total_score": 75,
        "grade": "🟡",
        "risk_type": "중립형",
        "weakest_point": "emergency_fund",
        "verdict": "PASS",
        "retry_count": 0
      }
    ]
  }
  ```

---

## 🚀 신규 처리 시작 가이드

```bash
# [Step 0 사전 준비 — Google Forms 응답 동기화]
# responses.csv가 최신 상태인지 확인한다.
# sync_forms.py는 prepare에서 자동 실행되지 않으므로 수동으로 먼저 실행한다.
# (Google Sheets API 키가 설정된 경우)
python scripts/sync_forms.py

# [Step 0-A 사전 준비] 안티그래비티 리포트 HTML을 아래 경로에 복사해두면 자동 파싱됨
# market_data/source_reports/투자리포트_YYYYMMDD_HHMM.html
# 파일이 없으면 Step 0-A는 건너뛰고 macro-analyst가 일반 원칙으로 대체함

# 1. 파이프라인 준비 — 미처리 고객 감지 + 시장 데이터 캐시 확인
python scripts/orchestrator.py prepare

# 2. prepare가 출력하는 단계별 커맨드를 순서대로 실행
#    각 에이전트 실행 → 파일 저장 → validate → PASS → 다음 단계

# 3. 완료 후 전체 현황 확인
python scripts/orchestrator.py status

# 4. 특정 고객 진행 상황 확인
python scripts/orchestrator.py status --client client_20260528_001
```

**Validation 에러 발생 시:** validate 출력의 `── Codex에게 전달할 수정 요청 ──` 블록을 해당 에이전트에게 그대로 주입하여 재작성 요청 → 저장 → validate 재실행 (최대 3회)

**reviewer FAIL 시:** Back-tracking 규칙에 따라 해당 에이전트부터 재실행 (아래 오류 처리 섹션 참조)

---

## 현재 구축 상태 (Phase 추적)

- [x] Phase 1: 기초 인프라 — 폴더 구조, AGENTS.md
- [x] Phase 2: 핵심 에이전트 3종 — kyc-collector, portfolio-designer, report-writer
- [x] Phase 3: 시장 분석 통합 — macro-analyst, stock-recommender
- [x] Phase 4: 품질 보증 — risk-scorer, reviewer
- [x] Phase 5-A: Method B 반자동화 — orchestrator.py CLI 구현 완료
  - `prepare` / `validate` / `normalize` / `macro-check` / `status` / `finalize` 6개 명령
  - 중간 파일 규칙: portfolio.json, stock_plan.json, risk_score.json 필수 저장
  - Pydantic 검증 에러 메시지 → Codex 자가 수정 루프 연결
- [x] Phase 5-A 안정성 보강 (수정 보완.txt 반영)
  - **파일 원자성:** utils.py의 `safe_write_json`을 모든 스크립트에 통일 적용 (data_fetcher, fetch_market_data, correlation_analyzer, value_screener, backtester, ticker_normalizer)
  - **Step 0 실행 순서 수정:** 0-A → 0-B → 0-C (기존 0-A → 0-C → 0-B에서 수정)
  - **unresolved 티커 감지:** normalize 후 파이프라인 개입 요청 메시지 출력
  - **StockPlanOutput 검증 허용 오차:** 1,000원 → max(10,000, 1%) 로 완화
  - **lump_sum_amount 필드 추가:** StockProduct에 일시납 배정액 필드 + total_lump_sum 검증
  - **ISA 유동성 잠금 보강:** short_term 목표에 ISA(3년 락업) 계좌도 차단
- [x] Phase 5-B: yfinance 중앙 집중식 가격 캐시 구현
  - `scripts/price_cache.py` 신규 생성 — `ensure_cached` / `get_close_series` / `get_info` / `load` 공개 API
  - `market_data/price_cache.json` (7일 TTL 퍼-티커) 에 통합 저장
  - data_fetcher / value_screener / correlation_analyzer / backtester 모두 price_cache 경유로 전환
  - correlation_analyzer의 `stock_history_cache.json` 독립 캐시 및 관련 함수 3개 제거
  - 효과: yfinance IP Rate Limit 위험 감소, 파이프라인 속도 개선
- [ ] Phase 5-C: 완전 자동화 — Codex API 연동 (call_agent_with_validation 함수 기구현, 추후 배선)
