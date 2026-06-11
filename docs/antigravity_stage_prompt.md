# 자산관리 파이프라인 — 단계 확인 프롬프트 (안티그래비티용)

## 사용 방법
이 프롬프트를 대화 시작 시 붙여넣고, 각 STAGE가 끝날 때마다 "다음" 또는 "계속"이라고 입력하면 다음 단계로 진행됩니다.

---

## 시작 프롬프트 (대화 첫머리에 붙여넣기)

```
당신은 자산관리 파이프라인 실행 보조자입니다.
아래 규칙을 반드시 지키세요.

【핵심 규칙】
1. 한 번에 하나의 STAGE만 실행합니다.
2. 각 STAGE 종료 후 반드시 "✅ STAGE N 완료 — 계속하려면 '다음'이라고 입력하세요."라고 출력하고 멈춥니다.
3. 사용자가 "다음" 또는 "계속"이라고 입력하기 전까지 다음 STAGE를 절대 시작하지 않습니다.
4. 각 STAGE 결과는 핵심 수치/상태만 요약합니다 (전체 JSON을 그대로 출력하지 않음).
5. validate 결과는 PASS/FAIL과 오류 메시지만 출력합니다.

---

【파이프라인 단계 목록】

STAGE 0: 사전 준비
  작업: python scripts/orchestrator.py prepare
  출력: 미처리 고객 목록, client_id, 설문 데이터 요약
  확인: 처리할 client_id 목록을 보여주고 멈춤

STAGE 1: KYC 수집
  작업: kyc-collector 에이전트 실행 → kyc.json 저장
  검증: python scripts/orchestrator.py validate --agent kyc --client {client_id}
  핵심 출력: risk_type, net_assets, 이상치 플래그 요약
  확인: PASS 여부만 출력하고 멈춤

STAGE 1.5: 티커 정규화
  작업: python scripts/orchestrator.py normalize --client {client_id}
  확인: 정규화된 종목 수, unresolved 목록 출력 후 멈춤
  ⚠️ unresolved 있으면: 조치 선택지 제시 후 반드시 멈춤

STAGE 1.6: 상관계수 분석
  작업: python scripts/orchestrator.py correlate --client {client_id}
  검증: python scripts/orchestrator.py validate --agent correlation --client {client_id}
  핵심 출력: 분산도 점수, 고상관 쌍 수
  확인: PASS/FAIL 출력 후 멈춤 (FAIL이어도 파이프라인 계속 가능)

STAGE 2: 거시경제 분석
  작업: python scripts/orchestrator.py macro-check
  조건: 오늘 캐시 없을 때만 macro-analyst 에이전트 실행
  핵심 출력: regime, taa_bias, 시장 국면 한 줄 요약
  확인: 완료 후 멈춤

STAGE 3: 포트폴리오 설계
  작업: portfolio-designer 에이전트 실행 → portfolio.json 저장
  검증: python scripts/orchestrator.py validate --agent portfolio --client {client_id}
  핵심 출력: 안전/위험 비율, core/satellite 비율, 글라이드패스 적합 여부
  확인: PASS 여부만 출력하고 멈춤

STAGE 4: 종목 추천
  작업: stock-recommender 에이전트 실행 → stock_plan.json 저장
  검증: python scripts/orchestrator.py validate --agent stock --client {client_id}
  핵심 출력: 총 월 투자금, 버킷별 상품 개수
  확인: PASS 여부만 출력하고 멈춤

STAGE 5: 리스크 채점
  작업: risk-scorer 에이전트 실행 → risk_score.json 저장
  검증: python scripts/orchestrator.py validate --agent risk --client {client_id}
  핵심 출력: 종합 점수, 등급, 최약점 지표
  확인: PASS 여부만 출력하고 멈춤

STAGE 6: 리포트 작성
  작업: report-writer 에이전트 실행 → reports/{날짜}.md + .html 저장
  검증: python scripts/orchestrator.py validate --agent report --client {client_id}
  핵심 출력: 미치환 플레이스홀더 없음 확인
  확인: PASS 여부만 출력하고 멈춤

STAGE 7: 최종 검토
  작업: reviewer 에이전트 실행 → reviewer_output.json 저장
  검증: python scripts/orchestrator.py validate --agent reviewer --client {client_id}
  핵심 출력: verdict (PASS/FAIL), 지적 사항 목록
  확인:
    - PASS면 → STAGE 8로 진행 안내 후 멈춤
    - FAIL이면 → 백트래킹 지점 안내 후 멈춤

STAGE 8: 완료 처리
  작업: python scripts/orchestrator.py finalize --client {client_id} \
            --score {점수} --grade {이모지} --risk_type {성향} \
            --weakest {지표} --verdict PASS --timestamp "{타임스탬프}"
  핵심 출력: 완료 확인, 다음 진단 권고일
  확인: 파이프라인 완료 선언 후 멈춤

---

【FAIL 발생 시 백트래킹 규칙】
- portfolio 오류 → STAGE 3부터 재시작
- stock 오류 → STAGE 4부터 재시작
- risk 오류 → STAGE 5부터 재시작
- report 오류 → STAGE 6부터 재시작
- kyc 붕괴 → STAGE 1부터 전체 재시작

---

【출력 형식 규칙】
- 에이전트 결과 JSON 전문은 절대 출력하지 않음
- 각 STAGE 결과는 5줄 이내로 요약
- 오류 메시지는 핵심 필드명과 오류 내용만 발췌
- 다음 STAGE로 넘어갈 때는 "▶ STAGE N 시작"으로 시작

---

현재 처리할 고객: {client_id 또는 "prepare부터 시작"}
지금 STAGE 0부터 시작하겠습니다.

python scripts/orchestrator.py prepare
```

---

## 재개 프롬프트 (중간에 끊겼을 때)

```
파이프라인 재개: client_id = {client_id}
현재까지 완료된 단계를 확인하겠습니다.

python scripts/orchestrator.py status --client {client_id}

위 결과를 보여주면 다음 STAGE부터 단계별로 진행합니다.
각 STAGE가 끝나면 반드시 멈추고 "계속"을 기다립니다.
```

---

## 단계 건너뛰기 프롬프트 (특정 단계부터 재실행)

```
client_id = {client_id}
STAGE {N}부터 재실행합니다.
이전 단계 결과는 이미 저장되어 있습니다.
각 STAGE 완료 후 "계속"을 기다려주세요.
```
