# 📋 자산관리 자동화 파이프라인 검토 및 병목현상 분석 보고서

본 보고서는 **자산관리 멀티 에이전트 시스템(v3.0 Final)**의 소스 코드와 오케스트레이터 파이프라인 구조를 분석하여 시스템의 작동 여부를 검증하고, 처리 속도 및 안정성 저하를 유발하는 병목 지점을 진단하여 향후 개선 방향과 대체 API 활용법을 제안하기 위해 작성되었습니다.

---

## 1. 파이프라인 작동 신뢰성 검토 (Architecture Validation)

현재 구현된 시스템 구조(`CLAUDE.md`, `orchestrator.py` 및 관련 스크립트)를 분석한 결과, **파이프라인의 논리적 흐름과 설계 자체는 매우 견고하게 구축되어 있습니다.**

### 주요 강점:
- **엄격한 Pydantic 스키마 검증 (`schemas.py`):** 에이전트의 중간 산출물(KYC, 포트폴리오, 추천 종목, 리스크 스코어, 리뷰어 출력 등)이 생성될 때마다 형식과 조건(예: 안전자산 + 위험자산 비중 = 100%)을 검증함으로써 환각 현상(Hallucination)을 효과적으로 제어합니다.
- **원자적 파일 쓰기 (`utils.py`의 `safe_write_json`):** 모든 스크립트에서 파일 쓰기 시 임시 파일(`.tmp`)을 생성한 후 덮어쓰는(Replace) 방식을 채택하여, 프로그램 중단 시 데이터가 손상되는 현상을 물리적으로 방지하고 있습니다.
- **중앙 집중식 가격 캐시 시스템 (`price_cache.py`):** 중복되는 `yfinance` 주가 다운로드 요청을 7일 주기 캐시 파일(`price_cache.json`)로 흡수하여 API 호출 횟수를 획기적으로 줄였습니다.
- **DLQ(Dead Letter Queue) 도입:** 실패한 고객 정보와 오류 사유를 `dlq.json`에 기록하고 성공 시 자동으로 지워주어 예외 관리가 원활합니다.

---

## 2. 핵심 병목 지점 및 성능 저하 유입 요인 (Bottlenecks)

코드 수준에서 분석한 결과, 데이터 수집 및 실행 구조에서 다음과 같은 **성능 저하 요인 및 병목 지점**이 식별되었습니다.

### 🚨 [병목 1] `price_cache.py` 내의 순차적 `yf.Ticker().info` 호출 (치명적)
- **발생 위치:** [price_cache.py:L191-L199](file:///g:/%EB%82%B4%20%EB%93%9C%EB%9D%BC%EC%9D%B4%EB%B8%8C/%EC%9E%90%EC%82%B0%EA%B4%80%EB%A6%AC%20%EC%9E%90%EB%8F%99%ED%99%94/scripts/price_cache.py#L191-L199)
- **상세 원인:**
  `ensure_cached` 함수는 여러 티커를 입력받아 일별 종가는 `yf.download`를 통해 단 한 번의 배치 호출로 고속 다운로드합니다. 그러나 배당률이나 PER 같은 펀더멘털 데이터를 위한 `info` 정보는 **개별 티커별로 루프를 돌며 동기식(Synchronous)으로 `yf.Ticker(ticker).info`를 호출**합니다.
- **성능 영향:**
  `yfinance`는 별도의 공식 API가 아닌 야후 파이낸스 웹사이트를 스크래핑하는 구조이므로, `.info` 호출 하나당 최소 1~2초가 소요됩니다.
  만약 `value_screener.py`의 전체 유니버스나 `trending_stocks.json`의 티커 30~50개가 캐시 만료(7일 경과)되거나 초기 실행될 경우, **약 1~2분의 대기 시간이 강제**되며, 연속적인 HTTP 요청으로 인해 야후 측으로부터 **IP 차단(HTTP 429 Rate Limit)을 당할 위험**이 매우 높습니다.

### ⚠️ [병목 2] 고객별 상관계수 분석 시 매번 수행되는 벤치마크 캐시 유효성 체크
- **발생 위치:** [correlation_analyzer.py:L265-L271](file:///g:/%EB%82%B4%20%EB%93%9C%EB%9D%BC%EC%9D%B4%EB%B8%8C/%EC%9E%90%EC%82%B0%EA%B4%80%EB%A6%AC%20%EC%9E%90%EB%8F%99%ED%99%94/scripts/correlation_analyzer.py#L265-L271)
- **상세 원인:**
  `_get_benchmark_returns` 함수는 포트폴리오 메트릭 계산을 위해 `SPY`, `069500.KS`, `^GSPC` 등 세 가지 벤치마크 데이터를 가져옵니다. 이때 고객 한 명의 상관분석(`correlate`)을 실행할 때마다 매번 `_pc.ensure_cached(bench_tickers)`를 동기 호출하여 유효성을 검사합니다.
- **성능 영향:**
  고객이 10명이면 동일한 3개 티커에 대해 캐시 유효성 연산을 10번 반복합니다. 캐시가 히트되면 큰 지연은 없으나, 캐시가 만료된 시점의 첫 실행 시점에는 동기식 다운로드 및 info 수집 루프가 물려 특정 고객 처리 단계에서 대기 시간이 급증하게 됩니다.

### 📉 [병목 3] `subprocess.run`을 통한 중복된 파이썬 인터프리터 기동 오버헤드
- **발생 위치:** [orchestrator.py:L318-L341](file:///g:/%EB%82%B4%20%EB%93%9C%EB%9D%BC%EC%9D%B4%EB%B8%8C/%EC%9E%90%EC%82%B0%EA%B4%80%EB%A6%AC%20%EC%9E%90%EB%8F%99%ED%99%94/scripts/orchestrator.py#L318-L341)
- **상세 원인:**
  오케스트레이터가 하위 태스크(`ticker_normalizer.py`, `correlation_analyzer.py`, `value_screener.py` 등)를 실행할 때 `subprocess.run([sys.executable, ...])`을 사용하여 매번 새로운 독립 파이썬 프로세스를 띄웁니다.
- **성능 영향:**
  - 윈도우 환경에서는 파이썬 인터프리터를 새로 구동하고 모듈을 초기 로딩(Import)하는 데 프로세스당 약 **0.3~0.8초의 기본 지연 시간**이 발생합니다.
  - 하나의 고객 파이프라인에서 여러 스크립트를 독립적으로 쪼개어 서브프로세스로 실행하므로 전체 고객 수가 늘어날수록 누적 기동 오버헤드가 커집니다.
  - 또한 서브프로세스로 실행 시 메모리에 올라와 있는 캐시 객체(`price_cache` 등)를 공유하지 못하고 계속 파일 디스크(I/O)에서 새로 읽어야 합니다.

### 🌐 [병목 4] 네트워크/SMTP 호출 시 타임아웃 미설정으로 인한 무한 대기 위험
- **발생 위치:** `send_report.py` (이메일 발송) 및 `data_fetcher.py` (FRED/ECOS API 호출)
- **상세 원인:**
  - `send_report.py`의 `smtplib.SMTP_SSL("smtp.gmail.com", 465)` 연결 시 명시적인 연결 타임아웃(`timeout` 파라미터)이 지정되어 있지 않습니다.
  - 파이썬 기본 소켓 타임아웃이 무한대(None)인 경우, 만약 SMTP 서버 응답이 없거나 인터넷 환경이 일시적으로 끊어지면 파이프라인 전체가 해당 단계에서 무한 대기 상태(Hang)로 머물게 됩니다.
  - `data_fetcher.py`의 외부 API 호출(`requests.get`) 중 일부는 `timeout=10`을 선언했으나 누락된 곳도 존재합니다.

---

## 3. Step 0 외부 데이터 수집의 '배치(Batch)' 최적화

고객 파이프라인 진입 전에 공용 데이터를 세팅하는 `data_fetcher.py` 등의 속도가 전체 시스템의 준비 시간을 결정합니다. 단일 종목(SKU)을 하나씩 조회(API Call)하면 네트워크 지연이 누적되어 병목이 발생합니다.

**해결책:** 워터 스파이더(Water Spider)가 물건을 한 번에 카트에 가득 담아 나르듯, 주가 데이터나 거시 경제 지표는 `asyncio`나 `yfinance`의 멀티스레딩 기능을 이용해 한 번에 묶어서(Batch) 가져와야 합니다.

```python
import yfinance as yf

# 기존: for문으로 하나씩 조회 (매우 느림)
for ticker in tickers:
    data = yf.Ticker(ticker).history(period="1y")

# 개선: yfinance 내장 병렬 다운로드 사용 (수십 배 빠름)
# tickers = "005930.KS 035420.KS AAPL MSFT"
data = yf.download(tickers, period="1y", threads=True)
```

---

## 4. 해외 대체 금융 API 발급 가이드

`yfinance`를 대체하기에 유용한 대표적인 3대 무료/저가 금융 데이터 API의 특징 및 키 발급 방법은 다음과 같습니다.

### 🔑 [대체 API 1] Alpha Vantage (알파 밴티지)
주가 가격 데이터 및 재무 메트릭(PER, 배당률 등)을 모두 깔끔한 JSON API 형식으로 제공합니다.
- **홈페이지:** [https://www.alphavantage.co/](https://www.alphavantage.co/)
- **발급 순서:**
  1. 홈페이지 메인 화면의 **"GET YOUR FREE API KEY"** 버튼을 누릅니다.
  2. 직업 형태(Investor, Developer 등), 이메일 주소, 소속 기관명을 간단히 적고 **"GET FREE API KEY"**를 클릭합니다.
  3. 화면에 즉시 API 키가 발급되어 표시되며, 기재한 이메일로도 확인 메일이 발송됩니다.
- **무료 티어 제약:** 분당 최대 25회 호출 가능. (최근 무료 티어에 일일 제한이 타이트하게 적용될 수 있으므로 대량 배치 조회 시 모니터링 필요).

### 🔑 [대체 API 2] Polygon.io (폴리곤)
매우 빠르고 안정적이며 풍부한 시장 데이터를 제공하는 가장 널리 사용되는 금융 API 중 하나입니다.
- **홈페이지:** [https://polygon.io/](https://polygon.io/)
- **발급 순서:**
  1. 메인 화면 우측 상단의 **"Get Started"** 또는 **"Sign Up"**을 클릭하여 회원 가입을 진행합니다.
  2. 로그인 후 사용자 대시보드(Dashboard)의 왼쪽 네비게이션 바에서 **"API Keys"** 메뉴를 선택합니다.
  3. 기본 생성된 API 키를 확인하거나, **"Create New Key"**를 눌러 새 키를 생성할 수 있습니다.
- **무료 티어 제약:** 분당 최대 5회 호출 가능. 무료 등급은 2년 미만의 역사적 데이터만 조회가 가능하며, 실시간 데이터가 약 15분 지연 제공됩니다.

### 🔑 [대체 API 3] Financial Modeling Prep (FMP)
손익계산서, 재무 비율(PER, PBR), 배당금 히스토리 등 펀더멘털 분석 데이터에 특화된 API입니다.
- **홈페이지:** [https://financialmodelingprep.com/](https://financialmodelingprep.com/)
- **발급 순서:**
  1. 우측 상단의 **"Sign Up"**을 눌러 회원 가입합니다.
  2. 가입 완료 및 로그인 후, 우측 상단 메인 대시보드(**Dashboard**) 화면으로 이동합니다.
  3. 대시보드 메인 화면 중앙 혹은 좌측 메뉴의 **"Developer" -> "API Keys"**에서 고유 API 키 값을 복사합니다.
- **무료 티어 제약:** 일일 최대 250회 호출 가능. 일부 고급 엔드포인트 및 다년도 과거 데이터는 유료 구독이 필요합니다.

---

## 5. 결론 및 권장 최적화 로드맵

현재 파이프라인은 신뢰할 수 있는 데이터 정제 기법과 안정 장치로 **비즈니스 로직 상 결함 없이 완성도 있게 동작**하고 있습니다.

다만 외부 수집 성능 극대화를 위해 향후 최적화 시 다음 두 가지를 먼저 적용해 보시는 것을 권장합니다.

1. **단기 처방:** `price_cache.py`에 **ThreadPoolExecutor (max_workers=5)**를 추가해 병렬 호출 구조로 전환하고, **Info 데이터의 캐시 만료를 30일로 연장**합니다.
2. **장기 처방:** `yfinance`에서 **Alpha Vantage** 또는 **Financial Modeling Prep (FMP)** API를 연동하도록 수집 코드를 공식 전환하여 스크래핑으로 인한 IP 밴 위험을 원천 해소합니다.
