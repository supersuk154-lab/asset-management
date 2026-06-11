# 병목 현상 완화 개선안

작성일: 2026-06-04

## 목적

현재 자산관리 자동화 파이프라인은 `orchestrator.py`가 단계별 검증과 상태 관리를 강제하고, 시장 데이터 수집 및 가격 캐시는 별도 스크립트가 담당하는 구조다. 이 문서는 나중에 반영하기 쉽도록 병목 가능성이 높은 지점과 개선 우선순위를 정리한다.

## 핵심 병목 후보

### 1. yfinance 및 외부 API 호출 집중

`data_fetcher.py`, `value_screener.py`, `correlation_analyzer.py`, `backtester.py`가 모두 가격 또는 종목 정보를 필요로 한다. Phase 5-B에서 `price_cache.py`로 중앙 캐시가 도입되어 중복 호출 위험은 줄었지만, 다음 병목은 여전히 남을 수 있다.

- 캐시 미스가 많은 첫 실행 시 여러 티커를 순차 다운로드할 가능성
- 티커별 `info` 조회가 가격 히스토리보다 느리고 실패율이 높을 가능성
- `correlation_analyzer.py`가 고객별 보유 종목마다 1년 일별 데이터를 요구하여 신규 고객이 많을수록 요청량 증가
- 네트워크 실패 시 재시도 대기 시간이 전체 파이프라인을 붙잡을 가능성

개선 방향:

- `price_cache.ensure_cached()`가 여러 티커를 한 번에 묶어 다운로드하도록 보장한다.
- 가격 히스토리와 `info` 캐시 TTL을 분리한다. 예: 가격 1일, 펀더멘털 7일 또는 30일.
- `info` 전체 조회 대신 필요한 필드만 수집하거나, 실패 시 즉시 정적 fallback으로 넘어가는 fast-fail 옵션을 둔다.
- 캐시 미스 티커 목록을 먼저 계산한 뒤 한 번의 배치 작업으로 채운다.
- 외부 API 호출 타임아웃을 짧게 두고, 실패 티커를 DLQ 또는 warning 목록에 기록한 뒤 고객 처리는 계속한다.

### 2. 고객별 상관계수 분석 반복

`correlation_analyzer.py`는 고객별 투자 종목을 기준으로 피어슨 상관계수 행렬을 계산한다. 보유 종목 조합이 여러 고객에게 반복될 경우 같은 티커 데이터와 유사한 계산이 반복될 수 있다.

개선 방향:

- 티커 가격 데이터 캐시는 이미 중앙화했으므로, 다음 단계로 `correlation_result_cache.json`을 추가한다.
- 캐시 키는 정렬된 티커 목록과 기간으로 만든다. 예: `005930.KS|000660.KS|AAPL:1y`.
- 동일 조합 또는 부분 조합이 반복되면 계산 결과를 재사용한다.
- 고객 수가 많은 정기 점검에서는 전체 고객의 티커 유니버스를 먼저 모아 가격 데이터를 선적재한 뒤 고객별 계산만 수행한다.

### 3. Step 0-A, 0-B, 0-C의 순차 실행 시간

현재 실행 순서는 정확성 때문에 `0-A -> 0-B -> 0-C`를 유지해야 한다. 특히 `0-C value_screener.py`는 `0-B data_fetcher.py`가 갱신한 `drawdown_pct`와 펀더멘털 데이터를 참조하므로 순서 변경은 위험하다.

개선 방향:

- 순서는 유지하되 각 단계 내부에서 독립 작업을 병렬화한다.
- `data_fetcher.py` 내부의 거시경제 API 호출(FRED, ECOS)과 종목 가격 갱신은 서로 의존성이 낮으므로 내부 병렬 실행 대상으로 분리한다.
- `value_screener.py`는 이미 갱신된 `trending_stocks.json`을 읽는 계산 단계이므로, I/O보다 계산 로직이 오래 걸리는지 측정 로그를 추가한다.
- `prepare` 출력에 단계별 소요 시간을 표시해 어느 단계가 실제 병목인지 확인한다.

### 4. 에이전트 단계별 수동 왕복

Method B 구조에서는 각 에이전트 결과 저장 후 `validate`를 실행하고 PASS를 확인해야 한다. 안정성은 높지만, 신규 고객이 여러 명일 때 수동 왕복이 큰 시간 비용이 된다.

개선 방향:

- Phase 5-C의 `call_agent_with_validation` 배선을 우선 적용한다.
- 각 에이전트 호출에 공통 self-correction 루프를 둔다.
- `validate` 실패 시 출력되는 `Codex에게 전달할 수정 요청` 블록을 자동으로 재주입한다.
- 최대 3회 실패 시 `logs/errors.log`와 DLQ에 기록하고 다음 고객으로 넘어간다.
- 자동화하더라도 최종 `reviewer` FAIL 라우팅 규칙은 기존 Back-tracking 규칙을 그대로 유지한다.

### 5. 파일 I/O와 JSON 원자적 쓰기

`safe_write_json` 도입으로 파일 손상 위험은 줄었지만, 고객 수가 많아지면 같은 파일을 반복적으로 읽고 쓰는 비용이 누적될 수 있다.

주요 대상:

- `logs/processed.json`
- `data/master_index.json`
- `market_data/price_cache.json`
- `market_data/trending_stocks.json`
- 고객별 `history.json`

개선 방향:

- `prepare` 시작 시 공통 상태 파일을 한 번 읽고 메모리에서 처리한 뒤 마지막에 한 번 저장한다.
- `price_cache.json`은 티커별 갱신 후 매번 저장하지 말고 배치 단위로 저장한다.
- 캐시 파일이 커질 경우 JSON 단일 파일 대신 티커별 shard 구조를 검토한다. 예: `market_data/price_cache/005930.KS.json`.
- 상태 파일 쓰기 전후에 파일 크기와 소요 시간을 debug 로그로 남긴다.

### 6. 리포트 생성과 placeholder 검증

`report-writer`는 Markdown과 HTML을 모두 생성하고, `validate --agent report`에서 미치환 `{{PLACEHOLDER}}`를 검사한다. 템플릿이 커지고 고객 수가 늘면 렌더링과 검증이 반복 비용이 될 수 있다.

개선 방향:

- 템플릿 파일은 고객별로 반복 로드하지 말고 한 실행 단위에서 캐싱한다.
- placeholder 목록을 템플릿에서 사전 추출하고, 렌더링 입력 데이터와 비교하는 preflight 검증을 추가한다.
- Markdown 생성 후 HTML 변환이 동일 프로세스 안에서 일어난다면 중간 결과를 명확히 재사용한다.
- PDF 생성이 필요한 경우에는 리포트 PASS 이후 비동기 후처리로 분리한다.

## 측정 로그 추가 제안

개선 전에는 먼저 실제 병목을 수치로 확인하는 것이 좋다. `orchestrator.py`에 단계별 타이머를 추가하면 된다.

기록 항목:

- `prepare_total_sec`
- `fetch_market_data_sec`
- `data_fetcher_sec`
- `value_screener_sec`
- `normalize_sec`
- `correlate_sec`
- `macro_check_sec`
- `validate_{agent}_sec`
- `report_validate_sec`
- `finalize_sec`

저장 위치:

- 간단 버전: `logs/performance.log`에 JSON Lines 형식으로 append
- 분석 버전: `logs/performance/YYYY-MM-DD.jsonl` 날짜별 분리

예시 형식:

```json
{"timestamp":"2026-06-04T09:00:00+09:00","client_id":"client_20260604_001","step":"correlate","elapsed_sec":3.42,"status":"PASS","cache_hit_ratio":0.86}
```

## 우선순위별 실행 계획

### P0: 측정 기반 확보

가장 먼저 `orchestrator.py`와 외부 데이터 수집 스크립트에 단계별 소요 시간 로그를 추가한다. 병목 개선은 추정으로 시작하더라도, 반영 여부는 반드시 측정값으로 판단해야 한다.

기대 효과:

- 실제로 느린 단계 식별
- 캐시 도입 효과 확인
- API 실패와 지연의 상관관계 파악

### P1: 외부 데이터 호출 최적화

`price_cache.py`를 중심으로 배치 다운로드, TTL 분리, fast-fail 정책을 보강한다. 파이프라인 전체 시간에서 가장 불안정한 부분은 네트워크 I/O일 가능성이 높다.

기대 효과:

- 첫 실행 지연 감소
- yfinance rate limit 위험 감소
- 고객 수 증가 시 처리 시간 증가폭 완화

### P2: Phase 5-C 자동 검증 루프 연결

`call_agent_with_validation`을 실제 `orchestrator.py agent` 흐름에 배선한다. 수동 단계가 줄어들면 처리량이 크게 개선된다.

기대 효과:

- 신규 고객 다건 처리 시간 감소
- validate 실패 대응 표준화
- DLQ 기반 운영 안정성 향상

### P3: 상관계수 결과 캐시

고객 수가 늘고 보유 종목 조합이 반복되기 시작하면 `correlation_result_cache.json` 또는 shard 캐시를 추가한다.

기대 효과:

- 정기 점검 시 반복 계산 감소
- 동일 포트폴리오 또는 유사 포트폴리오 고객 처리 속도 개선

### P4: 상태 파일 I/O 정리

`processed.json`, `master_index.json`, `history.json`, `price_cache.json`의 읽기/쓰기 횟수를 줄인다. 현재는 치명 병목보다는 규모가 커졌을 때 문제가 될 가능성이 높다.

기대 효과:

- 대량 고객 처리 시 안정성 향상
- Google Drive 동기화 폴더에서 파일 잠금 또는 지연 가능성 감소

## 구현 시 주의사항

- Step 0 실행 순서 `0-A -> 0-B -> 0-C`는 유지한다.
- Pydantic 검증은 생략하지 않는다. 병목 완화 대상은 검증 제거가 아니라 호출 자동화와 실패 복구 자동화다.
- `safe_write_json` 원칙은 유지한다.
- 금융 컴플라이언스 문구는 계속 `compliance_rules.json`에서만 읽는다.
- 캐시 fallback을 강화하더라도, 리포트에는 데이터 신선도와 fallback 사용 여부가 드러나야 한다.
- Google Drive 동기화 경로에서는 큰 JSON 파일을 너무 자주 쓰면 동기화 지연이 생길 수 있으므로 배치 저장을 우선한다.

## 빠른 적용 후보

1. `logs/performance.log` JSONL 타이머 추가
2. `price_cache.py` 배치 캐시 미스 처리 확인 및 보강
3. `info` 조회 TTL을 가격 TTL보다 길게 분리
4. `data_fetcher.py` 내부 API 호출 타임아웃과 fast-fail 적용
5. `correlation_analyzer.py` 결과 캐시 추가
6. `orchestrator.py agent`에 자동 validate/self-correction 루프 연결

## 결론

가장 먼저 손볼 곳은 외부 데이터 호출과 수동 검증 왕복이다. 정확성 통제를 유지하면서 병목을 줄이려면 `검증 생략`이 아니라 `측정 로그 추가 -> 캐시 효율 개선 -> 자동 검증 루프 연결` 순서로 가는 것이 안전하다.
