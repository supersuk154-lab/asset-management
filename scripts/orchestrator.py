"""
orchestrator.py — Method B 반자동화 파이프라인 보조 도구
========================================================

Claude(AI)가 판단·추론을 담당하고,
이 스크립트가 파일 경로·Pydantic 검증·저장·상태 관리를 강제(enforce)한다.

Claude는 파일 경로를 직접 기억하거나 수동으로 관리할 필요가 없다.
모든 규칙은 이 코드가 시행한다.

사용법
------
  python scripts/orchestrator.py prepare
      미처리 고객 목록 출력 + 0-A/0-B 스크립트 실행 (날짜 기반 캐시 확인)

  python scripts/orchestrator.py validate --agent kyc --client client_20260528_001
  python scripts/orchestrator.py validate --agent portfolio --client ...
  python scripts/orchestrator.py validate --agent stock --client ...
  python scripts/orchestrator.py validate --agent risk --client ...
  python scripts/orchestrator.py validate --agent reviewer --client ...
  python scripts/orchestrator.py validate --agent macro

  python scripts/orchestrator.py normalize --client client_20260528_001
      ticker_normalizer.py 실행

  python scripts/orchestrator.py macro-check
      macro_snapshot.json 날짜 유효성 확인

  python scripts/orchestrator.py status
  python scripts/orchestrator.py status --client client_20260528_001
      파이프라인 진행 상태 확인

  python scripts/orchestrator.py finalize --client client_20260528_001 \\
      --score 75 --grade "🟡" --risk_type "중립형" \\
      --weakest behavioral_gap --verdict PASS \\
      --timestamp "2026/05/28 09:30:00"
      history.json + processed.json 자동 업데이트

파이프라인 파일 경로 규칙
--------------------------
  kyc-collector     → data/clients/{id}/kyc.json
  portfolio-designer→ data/clients/{id}/portfolio.json
  stock-recommender → data/clients/{id}/stock_plan.json
  risk-scorer       → data/clients/{id}/risk_score.json
  reviewer          → data/clients/{id}/reviewer_output.json
  report-writer     → data/clients/{id}/reports/{YYYY-MM-DD}.md + .html
  macro-analyst     → market_data/macro_snapshot.json  (당일 전 고객 공유)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import subprocess
import sys
import time as _time_module
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
try:
    from llm_client import LLMClient
except Exception:
    LLMClient = None

from utils import safe_write_json, safe_read_json

# ─────────────────────────────────────
# Windows 콘솔 인코딩 (이모지·한글 크래시 방지)
# ─────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# ─────────────────────────────────────
# Pydantic 스키마 임포트
# ─────────────────────────────────────
try:
    from pydantic import ValidationError
    _scripts_dir = Path(__file__).parent
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))
    from schemas import (
        KYCOutput,
        MacroAnalystOutput,
        PortfolioDesignerOutput,
        StockPlanOutput,
        RiskScorerOutput,
        ReviewerOutput,
        CorrelationAnalysisOutput,
    )
    PYDANTIC_AVAILABLE = True
except ImportError as e:
    print(f"[Warning] Pydantic/schemas 임포트 실패: {e}")
    PYDANTIC_AVAILABLE = False

# ─────────────────────────────────────
# 경로 상수 (절대 경로 강제)
# ─────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "data"
MARKET_DIR  = BASE_DIR / "market_data"
LOGS_DIR    = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"

RESPONSES_CSV  = DATA_DIR / "responses.csv"
PROCESSED_JSON = LOGS_DIR / "processed.json"
MACRO_SNAPSHOT = MARKET_DIR / "macro_snapshot.json"
MACRO_RAW      = MARKET_DIR / "realtime_macro_raw.json"
CLIENTS_DIR    = DATA_DIR / "clients"
ERRORS_LOG     = LOGS_DIR / "errors.log"

TODAY = datetime.now().strftime("%Y-%m-%d")
DLQ_JSON = LOGS_DIR / "dlq.json"

# ─────────────────────────────────────
# 로깅 (표준 logging + 로테이션) — errors.log 유지하되 레벨/포맷/로테이션 추가
# ─────────────────────────────────────
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("orchestrator")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _err_handler = RotatingFileHandler(
        ERRORS_LOG, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    _err_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(_err_handler)


# ─────────────────────────────────────
# Dead Letter Queue (실패 고객 구조적 적재 → logs/dlq.json)
# 검증 실패 시 적재(retry_count 누적), 최종 성공(finalize) 시 제거.
# ─────────────────────────────────────
def _load_dlq() -> list:
    return safe_read_json(DLQ_JSON).get("failed", [])


def _save_dlq(items: list) -> None:
    safe_write_json(DLQ_JSON, {"failed": items})


def record_dlq(client_id: str, stage: str, reason: str) -> None:
    """실패 고객을 DLQ에 적재. 이미 있으면 retry_count 증가."""
    items = _load_dlq()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for it in items:
        if it.get("client_id") == client_id:
            it["retry_count"] = it.get("retry_count", 0) + 1
            it["last_stage"] = stage
            it["last_reason"] = reason[:300]
            it["updated_at"] = now
            _save_dlq(items)
            return
    items.append({
        "client_id": client_id, "first_stage": stage, "last_stage": stage,
        "last_reason": reason[:300], "retry_count": 1,
        "created_at": now, "updated_at": now,
    })
    _save_dlq(items)


def clear_dlq(client_id: str) -> bool:
    """고객이 최종 성공(finalize)하면 DLQ에서 제거. 제거 시 True."""
    items = _load_dlq()
    kept = [it for it in items if it.get("client_id") != client_id]
    if len(kept) != len(items):
        _save_dlq(kept)
        return True
    return False


# 에이전트 → (파일명, Pydantic 스키마 클래스)
AGENT_FILE_MAP = {
    "kyc":         ("kyc.json",                 KYCOutput                  if PYDANTIC_AVAILABLE else None),
    "correlation": ("correlation_analysis.json", CorrelationAnalysisOutput  if PYDANTIC_AVAILABLE else None),
    "portfolio":   ("portfolio.json",            PortfolioDesignerOutput    if PYDANTIC_AVAILABLE else None),
    "stock":       ("stock_plan.json",           StockPlanOutput            if PYDANTIC_AVAILABLE else None),
    "risk":        ("risk_score.json",           RiskScorerOutput           if PYDANTIC_AVAILABLE else None),
    "reviewer":    ("reviewer_output.json",      ReviewerOutput             if PYDANTIC_AVAILABLE else None),
}


def get_pipeline_steps(client_dir: Path = None) -> list:
    """실행 시점의 오늘 날짜 또는 kyc.json 생성 날짜 기반으로 파이프라인 단계 목록 반환."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    if client_dir:
        kyc_path = client_dir / "kyc.json"
        if kyc_path.exists():
            try:
                data = json.loads(kyc_path.read_text(encoding="utf-8"))
                created_at = data.get("created_at")
                if created_at:
                    date_str = created_at
            except Exception:
                pass
    return [
        ("kyc.json",                 "Step 1   kyc-collector"),
        ("correlation_analysis.json","Step 1.6 correlation-analyzer"),
        ("portfolio.json",           "Step 3   portfolio-designer"),
        ("stock_plan.json",          "Step 4   stock-recommender"),
        ("risk_score.json",          "Step 5   risk-scorer"),
        (f"reports/{date_str}.md",        "Step 6   report-writer (MD)"),
        (f"reports/{date_str}.html",      "Step 6   report-writer (HTML)"),
        (f"reports/{date_str}_easy.md",   "Step 6   report-writer (Easy MD)"),
        (f"reports/{date_str}_easy.html", "Step 6   report-writer (Easy HTML)"),
        ("reviewer_output.json",     "Step 7   reviewer"),
        ("history.json",             "완료     history 기록"),
    ]


# ═════════════════════════════════════
# 유틸 함수
# ═════════════════════════════════════

def load_processed() -> list:
    return safe_read_json(PROCESSED_JSON).get("processed", [])

def save_processed(processed_list: list):
    safe_write_json(PROCESSED_JSON, {"processed": processed_list})

def get_processed_timestamps() -> set:
    return {p["timestamp"] for p in load_processed()}

def get_processed_keys() -> tuple:
    """중복 처리 판정용 (조합키 집합, 구버전 ts 집합) 반환.

    - 조합키 = f"{timestamp}__{email}" (email 소문자) — 같은 초에 서로 다른
      사용자가 제출해도 구분된다.
    - 하위호환: email 필드가 없는 기존 processed 항목은 timestamp 단독으로
      두 번째 집합에 담아 종전과 동일하게 재처리를 막는다.
    """
    combo, ts_only = set(), set()
    for p in load_processed():
        ts = (p.get("timestamp") or "").strip()
        if not ts:
            continue
        email = (p.get("email") or "").strip().lower()
        if email:
            combo.add(f"{ts}__{email}")
        else:
            ts_only.add(ts)
    return combo, ts_only

def get_client_dir(client_id: str) -> Path:
    return CLIENTS_DIR / client_id

def find_client_by_email(email: str) -> Optional[tuple]:
    """
    이메일로 기존 고객을 탐색. 재상담 고객 식별에 사용.
    반환: (client_id, last_session_dict) or None
    - clients/ 하위 모든 kyc.json을 읽어 profile.email 비교 (소문자 정규화).
    - history.json이 있으면 마지막 세션도 함께 반환.
    """
    if not email or not CLIENTS_DIR.exists():
        return None
    email_lower = email.strip().lower()
    for client_dir in sorted(CLIENTS_DIR.iterdir(), reverse=True):  # 최신순
        if not client_dir.is_dir():
            continue
        kyc_path = client_dir / "kyc.json"
        if not kyc_path.exists():
            continue
        try:
            kyc = json.loads(kyc_path.read_text(encoding="utf-8"))
            stored = (kyc.get("profile", {}).get("email") or "").strip().lower()
            if stored and stored == email_lower:
                # 이전 세션 정보 로드
                last_session = None
                hist_path = client_dir / "history.json"
                if hist_path.exists():
                    hist = json.loads(hist_path.read_text(encoding="utf-8"))
                    sessions = hist.get("sessions", [])
                    if sessions:
                        last_session = sessions[-1]
                return (client_dir.name, last_session)
        except Exception:
            continue
    return None


def generate_client_id(date_str: str) -> str:
    """date_str='20260528' 기준으로 오늘 이미 생성된 client 수를 세어 순번 부여"""
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = [
        d for d in CLIENTS_DIR.iterdir()
        if d.is_dir() and d.name.startswith(f"client_{date_str}_")
    ]
    return f"client_{date_str}_{len(existing) + 1:03d}"

def is_macro_current() -> bool:
    """macro_snapshot.json의 'date' 필드가 오늘 날짜인지 확인"""
    return safe_read_json(MACRO_SNAPSHOT).get("date") == TODAY

def is_raw_current() -> bool:
    """realtime_macro_raw.json의 'date' 필드가 오늘 날짜인지 확인"""
    return safe_read_json(MACRO_RAW).get("date") == TODAY

# 스크립트별 타임아웃 (초) — 외부 API 호출이 많을수록 길게 설정
_SCRIPT_TIMEOUTS: dict[str, int] = {
    "data_fetcher.py":         600,   # FRED/ECOS API 4개 + yfinance 배치
    "fetch_market_data.py":    300,   # HTML 파싱 + yfinance
    "value_screener.py":       300,   # yfinance 배치
    "correlation_analyzer.py": 300,   # yfinance + 행렬 연산
    "backtester.py":           300,   # yfinance
    "send_report.py":          120,   # SMTP
    "ticker_normalizer.py":     60,   # 로컬 처리
}
_DEFAULT_SCRIPT_TIMEOUT = 120


def run_script(script_name: str, *args, _client_id: str = "") -> bool:
    """scripts/ 폴더의 Python 스크립트를 서브프로세스로 실행. True=성공.
    PYTHONIOENCODING=utf-8 주입 → Windows cp949 기본값으로 인한 한글 깨짐 방지.
    타임아웃(_SCRIPT_TIMEOUTS 또는 기본 120초) 초과 시 False 반환 + 에러 기록.
    소요 시간은 logs/performance.log에 자동 기록된다.
    """
    timeout = _SCRIPT_TIMEOUTS.get(script_name, _DEFAULT_SCRIPT_TIMEOUT)
    cmd = [sys.executable, str(SCRIPTS_DIR / script_name)] + list(args)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    _t0 = _time_module.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env,
            timeout=timeout,
        )
        elapsed = _time_module.monotonic() - _t0
        ok = result.returncode == 0
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if not ok and result.stderr.strip():
            print(f"[stderr] {result.stderr[:600].rstrip()}")
        log_performance(
            step=script_name.replace(".py", ""),
            elapsed_sec=elapsed,
            client_id=_client_id,
            status="ok" if ok else "fail",
        )
        return ok
    except subprocess.TimeoutExpired:
        elapsed = _time_module.monotonic() - _t0
        msg = f"스크립트 실행 시간 초과 ({timeout}초): {script_name}"
        print(f"  ⏱️  [타임아웃] {msg}")
        log_performance(script_name.replace(".py", ""), elapsed, _client_id, "timeout")
        log_error(msg, script_name)
        return False

def divider(char="═", width=60):
    print(char * width)

def log_error(message: str, context: str = "") -> None:
    """errors.log에 기록(표준 logging + 로테이션). client 컨텍스트면 DLQ에도 구조적 적재."""
    ctx = f"[{context}] " if context else ""
    try:
        logger.error(f"{ctx}{message}")
    except Exception as e:
        print(f"  [경고] errors.log 기록 실패: {e}")
    # 클라이언트 처리 실패는 Dead Letter Queue에도 적재 (재시도/사후 점검용)
    if context and "client" in context:
        client_id = context.split("/")[0]
        stage = context.split("/")[1] if "/" in context else "finalize"
        try:
            record_dlq(client_id, stage, message)
        except Exception:
            pass


PERF_LOG = LOGS_DIR / "performance.log"


def log_performance(step: str, elapsed_sec: float,
                    client_id: str = "", status: str = "ok",
                    extra: dict | None = None) -> None:
    """단계별 소요 시간을 logs/performance.log에 JSONL 형식으로 append.

    기록 항목: timestamp, client_id, step, elapsed_sec, status, extra
    extra: 캐시 히트율·재시도 횟수 등 단계별 부가 정보 dict.
    """
    record = {
        "timestamp":   datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "client_id":   client_id,
        "step":        step,
        "elapsed_sec": round(elapsed_sec, 3),
        "status":      status,
    }
    if extra:
        record.update(extra)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(PERF_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 성능 로그 기록 실패는 파이프라인을 중단시키지 않는다


# ═════════════════════════════════════
# 명령: prepare
# ═════════════════════════════════════

def cmd_prepare(args):
    """
    파이프라인 시작 전 준비:
    1. 0-A/0-B 스크립트 날짜 기반 캐시 확인 → 필요 시 실행
    2. responses.csv에서 미처리 행 추출
    3. client_id 및 폴더 생성
    4. Claude가 바로 사용할 수 있는 컨텍스트 출력
    """
    _prepare_t0 = _time_module.monotonic()
    divider()
    print(f"[PREPARE] {TODAY} 파이프라인 준비")
    divider()

    # 0-A: fetch_market_data.py (macro_snapshot 날짜 체크)
    if is_macro_current():
        print(f"[0-A] macro_snapshot.json 오늘 데이터 유효 ({TODAY}) → 스킵")
    else:
        old_date = safe_read_json(MACRO_SNAPSHOT).get("date", "파일 없음")
        print(f"[0-A] macro_snapshot 날짜: {old_date} → 갱신 필요. fetch_market_data.py 실행 중...")
        ok = run_script("fetch_market_data.py")
        print(f"[0-A] {'✅ 완료' if ok else '⚠️ 실패 (계속 진행)'}")

    # 0-B: data_fetcher.py (realtime_macro_raw 날짜 체크) — 0-C보다 먼저 실행해야 함
    # value_screener.py(0-C)가 trending_stocks.json의 drawdown_pct를 참조하므로
    # data_fetcher.py가 이 값을 먼저 채워둬야 종속성 순서가 올바름
    if is_raw_current():
        print(f"[0-B] realtime_macro_raw.json 오늘 데이터 유효 ({TODAY}) → 스킵")
    else:
        old_date = safe_read_json(MACRO_RAW).get("date", "파일 없음")
        print(f"[0-B] realtime_macro_raw 날짜: {old_date} → 갱신 필요. data_fetcher.py 실행 중...")
        ok = run_script("data_fetcher.py")
        print(f"[0-B] {'✅ 완료' if ok else '⚠️ 실패 (계속 진행 — macro-analyst가 일반 원칙으로 대체)'}")

    # 0-C: value_screener.py (가치투자 스크리닝 — 7일 이내 캐시 없으면 실행)
    # 0-B 이후 실행: trending_stocks.json의 최신 drawdown_pct가 반영된 상태에서 스크리닝
    VALUE_CACHE_DAYS = 7
    value_picks_path = MARKET_DIR / "value_picks.json"
    value_current = False
    value_last_date = "파일 없음"
    if value_picks_path.exists():
        try:
            vd = json.loads(value_picks_path.read_text(encoding="utf-8"))
            value_last_date = vd.get("date", "날짜 없음")
            last_dt = datetime.strptime(value_last_date, "%Y-%m-%d")
            now_dt  = datetime.strptime(TODAY, "%Y-%m-%d")
            value_current = (now_dt - last_dt).days < VALUE_CACHE_DAYS
        except Exception:
            pass
    if value_current:
        print(f"[0-C] value_picks.json {VALUE_CACHE_DAYS}일 이내 데이터 유효 ({value_last_date}) → 스킵")
    else:
        print(f"[0-C] value_picks.json 마지막 갱신: {value_last_date} → 재실행. value_screener.py 실행 중...")
        ok = run_script("value_screener.py")
        print(f"[0-C] {'✅ 완료' if ok else '⚠️ 실패 (계속 진행 — stock-recommender가 value_picks 없이 동작)'}")
    log_performance("prepare_step0", _time_module.monotonic() - _prepare_t0, status="ok")

    # responses.csv 읽기
    if not RESPONSES_CSV.exists():
        print(f"\n[ERROR] responses.csv 없음: {RESPONSES_CSV}")
        return

    processed_combo, processed_ts_only = get_processed_keys()
    new_clients = []

    # Google Sheets CSV는 Windows에서 CP949로 저장될 수 있음 → 자동 감지
    _csv_enc = "utf-8"
    for _enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            with open(RESPONSES_CSV, newline="", encoding=_enc) as _f:
                _f.read(512)
            _csv_enc = _enc
            break
        except (UnicodeDecodeError, LookupError):
            continue

    with open(RESPONSES_CSV, newline="", encoding=_csv_enc) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("타임스탬프", "").strip()
            email = row.get("이메일", "").strip().lower()
            # 중복 판정: 조합키(ts__email, 신버전) 또는 ts 단독(구버전 데이터) 매칭 시 스킵.
            # 같은 초에 서로 다른 사용자가 제출해도 email 로 구분되어 누락되지 않는다.
            combo_key = f"{ts}__{email}" if email else ts
            if not ts or combo_key in processed_combo or ts in processed_ts_only:
                continue
            try:
                dt = datetime.strptime(ts, "%Y/%m/%d %H:%M:%S")
            except ValueError:
                print(f"[Warning] 타임스탬프 파싱 실패, 건너뜀: '{ts}'")
                continue

            date_str = dt.strftime("%Y%m%d")
            client_id = generate_client_id(date_str)
            client_dir = get_client_dir(client_id)
            client_dir.mkdir(parents=True, exist_ok=True)
            (client_dir / "reports").mkdir(exist_ok=True)

            # 재상담 고객 탐지 (이메일로 기존 고객 검색) — email 은 위에서 추출됨
            prev_info = find_client_by_email(email) if email else None

            # 재진단 고객: previous_session.json 을 새 디렉터리에 저장
            # → report-writer 가 파일로 직접 읽어 비교 섹션을 생성 (컨텍스트 전달 불필요)
            if prev_info and prev_info[1]:
                safe_write_json(client_dir / "previous_session.json", prev_info[1])

            new_clients.append({
                "client_id": client_id,
                "timestamp": ts,
                "email": email,
                "prev_client_id": prev_info[0] if prev_info else None,
                "prev_session": prev_info[1] if prev_info else None,
                "row": dict(row),
            })

    if not new_clients:
        print("\n✅ 미처리 고객 없음. 모든 응답이 처리 완료되었습니다.")
        return

    new_count = len(new_clients)
    revisit_count = sum(1 for c in new_clients if c.get("prev_client_id"))
    divider()
    print(f"신규 고객 {new_count}명 발견  (초진: {new_count - revisit_count}명 / 재상담: {revisit_count}명)")
    divider()

    for i, c in enumerate(new_clients, 1):
        cid = c["client_id"]
        is_revisit = bool(c.get("prev_client_id"))
        label = "🔄 재상담" if is_revisit else "🆕 초진"
        print(f"\n[{i}/{new_count}] {cid}  {label}")
        print(f"  타임스탬프: {c['timestamp']}")
        if c.get("email"):
            print(f"  이메일:     {c['email']}")
        if is_revisit:
            prev = c["prev_client_id"]
            sess = c.get("prev_session") or {}
            prev_date  = sess.get("date", "?")
            prev_score = sess.get("total_score", "?")
            prev_grade = sess.get("grade", "")
            print(f"  ↩️  이전 진단: {prev}  ({prev_date} / {prev_score}점 {prev_grade})")
            print(f"      → kyc-collector와 report-writer에 이전 client_id를 전달해 비교 섹션을 생성하세요.")
        print(f"  저장 경로:  {get_client_dir(cid)}")
        print(f"  설문 데이터:")
        for k, v in c["row"].items():
            if v and k != "타임스탬프":
                print(f"    {k}: {v}")

    divider()
    today = datetime.now().strftime("%Y-%m-%d")
    print("\n다음 단계 (Claude 지시 순서):")
    for i, c in enumerate(new_clients, 1):
        cid = c["client_id"]
        print(f"\n  [{i}] {cid}")
        print(f"    1. kyc-collector 실행 → kyc.json 저장")
        print(f"    2. python scripts/orchestrator.py validate --agent kyc --client {cid}")
        print(f"    3. python scripts/orchestrator.py normalize --client {cid}")
        print(f"    3.5. python scripts/orchestrator.py correlate --client {cid}")
        print(f"         python scripts/orchestrator.py validate --agent correlation --client {cid}")
        print(f"    4. python scripts/orchestrator.py macro-check  (필요 시 macro-analyst 실행)")
        print(f"    5. portfolio-designer → portfolio.json 저장")
        print(f"       python scripts/orchestrator.py validate --agent portfolio --client {cid}")
        print(f"    6. stock-recommender → stock_plan.json 저장")
        print(f"       python scripts/orchestrator.py validate --agent stock --client {cid}")
        print(f"    7. risk-scorer → risk_score.json 저장")
        print(f"       python scripts/orchestrator.py validate --agent risk --client {cid}")
        print(f"    8. report-writer → reports/{today}.md + .html 저장")
        if c.get("prev_session"):
            print(f"       ↩️  재진단: previous_session.json 저장됨 → report-writer가 자동으로 읽어 비교 섹션 생성")
        else:
            print(f"       (신규 고객 — previous_session.json 없음, 비교 섹션 생략)")
        print(f"    8.5. python scripts/orchestrator.py validate --agent report --client {cid}")
        print(f"    9. reviewer → reviewer_output.json 저장")
        print(f"       python scripts/orchestrator.py validate --agent reviewer --client {cid}")
        print(f"   10. python scripts/orchestrator.py finalize --client {cid} \\")
        print(f"           --score <점수> --grade <이모지> --risk_type <성향> \\")
        print(f"           --weakest <지표> --verdict PASS --timestamp \"{c['timestamp']}\"")
    divider()


# ═════════════════════════════════════
# 명령: validate
# ═════════════════════════════════════

def cmd_validate(args):
    """
    에이전트 출력 파일을 Pydantic으로 검증한다.
    PASS → 다음 단계 진행 / FAIL → 에러 메시지를 Claude에게 그대로 전달
    """
    agent     = args.agent.lower()
    client_id = args.client or ""

    # macro는 클라이언트 무관 (공유 파일)
    if agent == "macro":
        _validate_file(
            "macro", MACRO_SNAPSHOT,
            MacroAnalystOutput if PYDANTIC_AVAILABLE else None,
        )
        return

    # report 는 .md/.html 텍스트 → 미치환 플레이스홀더 검사 (Pydantic 무관, Fail-Fast)
    if agent == "report":
        if not client_id:
            print(f"[ERROR] --client 필수 (agent=report)")
            return
        _validate_report(client_id, getattr(args, "date", "") or "")
        return

    if agent not in AGENT_FILE_MAP:
        print(f"[ERROR] 알 수 없는 에이전트: '{agent}'")
        print(f"  가능한 값: {', '.join(AGENT_FILE_MAP)} | macro")
        return

    if not client_id:
        print(f"[ERROR] --client 필수 (agent={agent})")
        return

    filename, schema_class = AGENT_FILE_MAP[agent]
    file_path = get_client_dir(client_id) / filename
    _validate_file(agent, file_path, schema_class, client_id)


def _validate_file(agent: str, file_path: Path, schema_class, client_id: str = ""):
    """파일 로드 → JSON 파싱 → Pydantic 검증 → 결과 출력 + errors.log 기록"""
    divider("─")
    print(f"[VALIDATE] {agent}")
    print(f"  파일: {file_path}")
    divider("─")

    log_ctx = f"{client_id}/{agent}" if client_id else agent
    _t0 = _time_module.monotonic()

    if not file_path.exists():
        msg = f"파일 없음: {file_path}"
        print(f"  ❌ {msg}")
        print(f"     → 에이전트를 먼저 실행하고 파일을 저장하세요.")
        log_error(msg, log_ctx)
        log_performance(f"validate_{agent}", _time_module.monotonic() - _t0, client_id, "file_missing")
        return

    # JSON 파싱
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        msg = f"JSON 파싱 오류: {e}"
        print(f"  ❌ {msg}")
        log_error(msg, log_ctx)
        _feedback("JSON 형식이 잘못되었습니다. 중괄호/따옴표/쉼표를 점검하고 다시 저장하세요.")
        log_performance(f"validate_{agent}", _time_module.monotonic() - _t0, client_id, "json_error")
        return

    # Pydantic 검증
    if schema_class and PYDANTIC_AVAILABLE:
        try:
            schema_class.model_validate(data)
            elapsed = _time_module.monotonic() - _t0
            print(f"  ✅ PASS — Pydantic 검증 통과")
            log_performance(f"validate_{agent}", elapsed, client_id, "pass")
            _print_summary(agent, data, client_id)
        except ValidationError as e:
            elapsed = _time_module.monotonic() - _t0
            msg = f"Pydantic 검증 실패 ({e.error_count()}개 오류)"
            print(f"  ❌ FAIL — {msg}")
            log_error(msg, log_ctx)
            log_performance(f"validate_{agent}", elapsed, client_id, "fail",
                            {"error_count": e.error_count()})
            _feedback_pydantic(e, file_path.name)
    else:
        elapsed = _time_module.monotonic() - _t0
        # 스키마 없거나 Pydantic 미설치 → JSON 유효성만 체크
        print(f"  ✅ PASS — JSON 형식 유효 (스키마 검증 없음)")
        print(f"  최상위 키: {', '.join(str(k) for k in data.keys())}")
        log_performance(f"validate_{agent}", elapsed, client_id, "pass_no_schema")


def _validate_report(client_id: str, date_str: str = "") -> None:
    """report-writer 산출 .md/.html 에 미치환 {{PLACEHOLDER}} 가 남았는지 검사.

    reviewer(LLM) 호출 전에 Python 정규식으로 Fail-Fast 하여 토큰·시간을 절약한다.
    date_str 미지정 시 reports/ 의 가장 최근 .md 날짜를 사용한다.
    """
    divider("─")
    print(f"[VALIDATE] report")
    client_dir = get_client_dir(client_id)
    reports_dir = client_dir / "reports"
    if not date_str:
        # 날짜 자동탐지 시 초보자용 Easy 리포트(_easy.md)는 제외 — stem이 "{date}_easy"가 되어
        # 잘못된 date_str을 만들기 때문. 정식 리포트(.md)만으로 최신 날짜를 판정한다.
        mds = sorted(
            p for p in reports_dir.glob("*.md")
            if not p.stem.endswith("_easy")
        ) if reports_dir.exists() else []
        date_str = mds[-1].stem if mds else datetime.now().strftime("%Y-%m-%d")
    print(f"  대상 날짜: {date_str}")
    log_ctx = f"{client_id}/report"
    # 정식 리포트(.md/.html) + 초보자용 Easy 리포트(_easy.md/.html) 모두 미치환 검사 대상.
    targets = [
        reports_dir / f"{date_str}.md",
        reports_dir / f"{date_str}.html",
        reports_dir / f"{date_str}_easy.md",
        reports_dir / f"{date_str}_easy.html",
    ]
    pat = re.compile(r"\{\{.*?\}\}")
    checked_any = False
    all_ok = True
    for path in targets:
        print(f"  파일: {path}")
        if not path.exists():
            print(f"  ⚠️  파일 없음 (건너뜀)")
            continue
        checked_any = True
        unresolved = pat.findall(path.read_text(encoding="utf-8"))
        if unresolved:
            all_ok = False
            uniq = sorted(set(unresolved))
            msg = f"{path.name}: 치환되지 않은 변수 {len(uniq)}종 발견 {uniq[:20]}"
            print(f"  ❌ FAIL — {msg}")
            log_error(msg, log_ctx)
            _feedback(
                f"리포트 {path.name} 에 미치환 플레이스홀더가 남아있습니다: {uniq[:20]}. "
                "각 변수를 소스 JSON 수치로 치환하여 다시 저장하세요."
            )
        else:
            print(f"  ✅ PASS — {path.name} 미치환 플레이스홀더 없음")
    if not checked_any:
        print(f"  ❌ 검사할 리포트 파일이 없습니다 ({date_str}). report-writer를 먼저 실행하세요.")
        log_error(f"report 파일 없음 ({date_str})", log_ctx)
    elif all_ok:
        print(f"  ✅ 전체 PASS — 모든 리포트 치환 완료")


def _print_summary(agent: str, data: dict, client_id: str = ""):
    """검증 통과 시 핵심 수치 요약 출력"""
    if agent == "kyc":
        a = data.get("assets", {})
        p = data.get("profile", {})
        f = data.get("flags") or {}
        print(f"  │ risk_type: {p.get('risk_type')} | "
              f"risk_conflict: {data.get('status', {}).get('risk_conflict')}")
        print(f"  │ net_assets: {a.get('net_assets', 0):,}원 | "
              f"emergency_months: {f.get('emergency_months', 'N/A')}")
    elif agent == "correlation":
        score = data.get("portfolio_diversification_score", "N/A")
        detected = data.get("pseudo_diversification_detected", False)
        n_high = len(data.get("high_correlation_pairs", []))
        print(f"  │ 분산도 점수: {score}점 | "
              f"가짜 분산: {'⚠️ 감지됨' if detected else '✅ 정상'} | "
              f"고상관 쌍: {n_high}개")
        fallback = data.get("fallback_used", False)
        if fallback:
            print(f"  │ ※ 섹터 기반 정적 fallback 사용됨")
    elif agent == "macro":
        print(f"  │ regime: {data.get('regime')} | taa_bias: {data.get('taa_bias')}")
        print(f"  │ sentiment: {data.get('market_sentiment')} | "
              f"fear_greed: {data.get('fear_greed_index')}")
    elif agent == "portfolio":
        s, r = data.get("safe_pct", 0), data.get("risky_pct", 0)
        ok = "✅" if abs(s + r - 100) < 0.01 else "❌ 합계 오류!"
        core, sat = data.get("core_pct", 0), data.get("satellite_pct", 0)
        print(f"  │ safe: {s}% + risky: {r}% = {s + r}% {ok}")
        print(f"  │ core: {core}% / satellite: {sat}%")
        # 글라이드 패스 검증 (client_id 있을 때만)
        if client_id:
            _validate_glide_path(client_id, r)
    elif agent == "stock":
        total  = data.get("total_monthly", 0)
        n_safe = len(data.get("safe_products", []))
        n_core = len(data.get("core_products", []))
        n_sat  = len(data.get("satellite_products", []))
        print(f"  │ total_monthly: {total:,}원 | "
              f"safe: {n_safe}개 / core: {n_core}개 / satellite: {n_sat}개")
    elif agent == "risk":
        weakest = _find_weakest(data)
        print(f"  │ score: {data.get('total_score')}점 {data.get('grade')} | weakest: {weakest}")
    elif agent == "reviewer":
        print(f"  │ verdict: {data.get('verdict')} | "
              f"report_confirmed: {data.get('report_confirmed')}")


def _validate_glide_path(client_id: str, actual_risky_pct: float):
    """
    TDF 글라이드 패스 공식 기반으로 portfolio.json의 risky_pct가
    연령 기준 허용 범위(±10%p) 내에 있는지 검증.
    """
    try:
        # risk_calculator의 글라이드 패스 함수 임포트
        _sc_dir = Path(__file__).parent
        if str(_sc_dir) not in sys.path:
            sys.path.insert(0, str(_sc_dir))
        from risk_calculator import calc_glide_path_target

        kyc_path = get_client_dir(client_id) / "kyc.json"
        if not kyc_path.exists():
            return

        kyc = json.loads(kyc_path.read_text(encoding="utf-8"))
        age               = kyc.get("profile", {}).get("age_midpoint", 35)
        goal_type         = kyc.get("profile", {}).get("gbi_goal_type", "retirement")
        job_type          = kyc.get("profile", {}).get("job_type", "급여소득자")
        goal_years        = kyc.get("profile", {}).get("goal_years_remaining")

        gp = calc_glide_path_target(age, goal_type, job_type, goal_years)
        target = gp["target_risky_pct"]
        upper  = gp["upper_limit"]
        lower  = gp["lower_limit"]

        if actual_risky_pct > upper:
            print(f"  │ ⚠️ [글라이드패스] risky {actual_risky_pct}% > 연령 허용 상한 {upper}%")
            print(f"  │    {age}세 기준 목표 {target}% (±10%p 범위: {lower}~{upper}%)")
            print(f"  │    → portfolio-designer에 '연령 대비 위험자산 비중 과다' 피드백 검토")
        elif actual_risky_pct < lower:
            print(f"  │ ⚠️ [글라이드패스] risky {actual_risky_pct}% < 연령 허용 하한 {lower}%")
            print(f"  │    {age}세 기준 목표 {target}% (±10%p 범위: {lower}~{upper}%)")
            print(f"  │    → 지나치게 보수적. risk_conflict 또는 목표 보정 확인 권장")
        else:
            print(f"  │ ✅ [글라이드패스] risky {actual_risky_pct}% ∈ [{lower}~{upper}%] (연령 {age}세 적합)")

        if gp.get("hard_cap_applied"):
            cap = gp["hard_cap_value"]
            years = goal_years or "미지정"
            print(f"  │ ℹ️  [하드캡] 목표기간 {years}년 → 위험자산 최대 {cap}% 상한 적용됨")
    except Exception as e:
        print(f"  │ [글라이드패스 검증 스킵] {e}")


def _find_weakest(risk_data: dict) -> str:
    details = risk_data.get("details") or {}
    if not details:
        return "N/A"
    return min(details, key=lambda k: details[k].get("score", 99))

def _feedback(msg: str):
    print(f"\n  ── Claude에게 전달할 수정 요청 ──")
    print(f"  {msg}")

def _feedback_pydantic(e: "ValidationError", filename: str):
    print(f"\n  ── Claude에게 전달할 수정 요청 ──")
    print(f"  {filename}에 다음 오류가 있습니다. 수정 후 다시 저장하세요:\n")
    for err in e.errors():
        loc  = " → ".join(str(l) for l in err["loc"])
        msg  = err["msg"]
        inp  = err.get("input", "N/A")
        print(f"  [{loc}]")
        print(f"    오류: {msg}")
        print(f"    입력값: {inp}\n")


# ═════════════════════════════════════
# 명령: normalize
# ═════════════════════════════════════

def cmd_normalize(args):
    """ticker_normalizer.py 실행"""
    client_id = args.client
    print(f"[NORMALIZE] {client_id} 티커 정규화 실행 중...")
    ok = run_script("ticker_normalizer.py", client_id, _client_id=client_id)
    if ok:
        kyc_path = get_client_dir(client_id) / "kyc.json"
        print(f"  ✅ 완료 → {kyc_path} 업데이트됨")
        print(f"  ※ normalize 후 kyc.json이 변경되었습니다. validate --agent kyc로 재검증을 권장합니다.")

        # unresolved 티커 감지 → 파이프라인 진행 전 사용자 개입 요청
        try:
            kyc_data = json.loads(kyc_path.read_text(encoding="utf-8"))
            investments = kyc_data.get("assets", {}).get("investments", [])
            unresolved = [inv.get("raw_name", "?") for inv in investments if inv.get("match_type") == "unresolved"]
            if unresolved:
                print()
                print("  ⚠️  [UNRESOLVED 티커 감지] 아래 종목을 인식하지 못했습니다:")
                for name in unresolved:
                    print(f"     - {name}")
                print()
                print("  → 조치 옵션 (하나를 선택하세요):")
                print("    [A] ticker_normalizer.py의 ALIAS_MAP에 수동으로 매핑을 추가한 뒤")
                print("        normalize 재실행 → 정확한 티커로 재분석")
                print("    [B] 해당 종목을 '기타 자산'으로 분류하고 파이프라인 계속 진행")
                print("        (portfolio-designer에게 unresolved 종목을 기타/현금성 자산으로 처리하도록 지시)")
                print()
                print("  ⛔ 조치 없이 그대로 진행하면 LLM이 없는 티커를 추천하거나")
                print("     correlation_analyzer가 에러를 낼 수 있습니다.")
        except Exception:
            pass
    else:
        msg = f"ticker_normalizer.py 실패 (client={client_id})"
        print(f"  ❌ 실패. ticker_normalizer.py 로그를 확인하세요.")
        log_error(msg, client_id)


# ═════════════════════════════════════
# 명령: correlate
# ═════════════════════════════════════

def cmd_correlate(args):
    """correlation_analyzer.py 실행 → correlation_analysis.json 생성"""
    client_id = args.client
    print(f"[CORRELATE] {client_id} 상관계수 분석 실행 중...")
    ok = run_script("correlation_analyzer.py", client_id, _client_id=client_id)
    if ok:
        out_path = get_client_dir(client_id) / "correlation_analysis.json"
        print(f"  ✅ 완료 → {out_path}")
        print(f"  ※ validate --agent correlation --client {client_id} 로 결과를 검증하세요.")
    else:
        msg = f"correlation_analyzer.py 실패 (client={client_id})"
        print(f"  ❌ 실패. correlation_analyzer.py 로그를 확인하세요.")
        print(f"  ※ yfinance 미설치 시에도 섹터 기반 fallback으로 결과 파일이 생성됩니다.")
        log_error(msg, client_id)


# ═════════════════════════════════════
# 명령: macro-check
# ═════════════════════════════════════

def cmd_macro_check(args):
    """macro_snapshot.json 날짜 확인. 오래됐으면 macro-analyst 재실행 요청."""
    divider("─")
    print("[MACRO-CHECK] macro_snapshot.json 날짜 확인")
    divider("─")
    if is_macro_current():
        print(f"  ✅ 오늘({TODAY}) 데이터 유효 → macro-analyst 호출 불필요")
        print(f"     ※ 호출 후 validate --agent macro로 채워진 필드를 검증하세요.")
    else:
        old_date = safe_read_json(MACRO_SNAPSHOT).get("date", "파일 없음")
        print(f"  ⚠️  현재 날짜: {old_date} (오늘: {TODAY})")
        print(f"  → macro-analyst 에이전트를 실행하여 macro_snapshot.json을 갱신하세요.")
        print(f"  → 갱신 완료 후: python scripts/orchestrator.py validate --agent macro")


# ═════════════════════════════════════
# 명령: status
# ═════════════════════════════════════

def cmd_status(args):
    if args.client:
        _status_single(args.client)
    else:
        _status_all()

def _status_single(client_id: str):
    client_dir = get_client_dir(client_id)
    steps = get_pipeline_steps(client_dir)
    divider()
    print(f"[STATUS] {client_id}")
    divider()
    if not client_dir.exists():
        print(f"  ❌ 디렉토리 없음: {client_dir}")
        return
    for filename, label in steps:
        exists = (client_dir / filename).exists()
        print(f"  {'✅' if exists else '⬜'} {label}")
    # 다음 할 일 추천
    for filename, label in steps:
        if not (client_dir / filename).exists():
            print(f"\n  ▶ 다음 단계: {label}")
            break
    else:
        print(f"\n  🎉 파이프라인 완료!")
    print()

def _status_all():
    if not CLIENTS_DIR.exists() or not any(CLIENTS_DIR.iterdir()):
        print("처리된 고객 없음.")
        return
    divider()
    print(f"[STATUS] 전체 고객 현황")
    divider()
    clients = sorted(d for d in CLIENTS_DIR.iterdir() if d.is_dir())
    for cd in clients:
        steps = get_pipeline_steps(cd)
        total = len(steps)
        done = sum(1 for f, _ in steps if (cd / f).exists())
        pct  = int(done / total * 100)
        bar  = "█" * done + "░" * (total - done)
        done_str = "🎉 완료" if done == total else f"진행 중 ({pct}%)"
        print(f"  {cd.name}  [{bar}]  {done_str}")
    print()


# ═════════════════════════════════════
# 헬퍼: trending_snapshot 추출
# ═════════════════════════════════════

def _extract_trending_snapshot(client_dir: Path) -> list:
    """
    finalize 시점에 trending_stocks.json의 상위 종목 현재가를 저장한다.
    backtester.py가 이 스냅샷을 읽어 추천 이후 실제 수익률을 역산한다.

    yfinance 미설치 시 가격 없이 티커만 저장 (backtester에서 가격 재계산 가능).
    """
    trending_path = MARKET_DIR / "trending_stocks.json"
    if not trending_path.exists():
        return []

    try:
        data   = json.loads(trending_path.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
    except Exception:
        return []

    # 티커가 확정된 종목만 (fetch_status == "ok") 상위 5개
    valid = [s for s in stocks if s.get("fetch_status") == "ok" and s.get("ticker_used")][:5]
    if not valid:
        return []

    snapshot = []
    for s in valid:
        ticker = s.get("ticker_used")
        name   = s.get("name", ticker)
        price  = s.get("current_price")  # data_fetcher가 이미 계산해둔 값

        if price:
            snapshot.append({
                "ticker":                  ticker,
                "name":                    name,
                "price_at_recommendation": price,
                "date":                    TODAY,
            })

    if snapshot:
        print(f"  📸 trending_snapshot: {len(snapshot)}개 종목 가격 저장 (backtester용)")

    return snapshot


# ═════════════════════════════════════
# 명령: screen
# ═════════════════════════════════════

def cmd_screen(args):
    """value_screener.py 실행 → value_picks.json 생성"""
    force = getattr(args, "force", False)
    print(f"[SCREEN] 가치투자 스크리닝 실행 중 ({'강제 재실행' if force else '캐시 확인'})...")
    script_args = ["--force"] if force else []
    ok = run_script("value_screener.py", *script_args)
    out_path = MARKET_DIR / "value_picks.json"
    if ok and out_path.exists():
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            n = data.get("picks_count", 0)
            print(f"  ✅ value_picks.json: {n}개 종목 선별됨")
            contrarian = sum(1 for p in data.get("picks", []) if p.get("contrarian"))
            print(f"     역발상 후보: {contrarian}개 / 화제종목 중 저평가: {n - contrarian}개")
        except Exception:
            print("  ✅ 완료")
    else:
        print(f"  ⚠️  실패 또는 결과 없음 — value_screener.py 로그 확인")


# ═════════════════════════════════════
# 명령: backtest
# ═════════════════════════════════════

def cmd_backtest(args):
    """backtester.py 실행 → 추천 종목 성과 추적"""
    client_id = getattr(args, "client", None)
    print(f"[BACKTEST] 성과 추적 실행 중{' (' + client_id + ')' if client_id else ' (전체 고객)'}...")
    script_args = []
    if client_id:
        script_args = ["--client", client_id]
    ok = run_script("backtester.py", *script_args)
    summary_path = MARKET_DIR / "backtest_summary.json"
    if ok and summary_path.exists():
        try:
            summ = json.loads(summary_path.read_text(encoding="utf-8"))
            beat = summ.get("overall_beat_rate_pct")
            n    = summ.get("total_picks_analyzed", 0)
            print(f"  ✅ 추적 완료: {n}개 종목")
            if beat is not None:
                grade = "✅ 우수" if beat >= 55 else ("⚠️ 보통" if beat >= 45 else "❌ 미흡")
                print(f"     시스템 KOSPI 초과율: {beat}% {grade}")
        except Exception:
            print("  ✅ 완료 — market_data/backtest_summary.json 확인")
    else:
        print("  ⚠️  실패 — backtester.py 로그 확인")


# ═════════════════════════════════════
# 명령: finalize
# ═════════════════════════════════════

def cmd_finalize(args):
    """
    reviewer PASS 확정 후 마무리:
    1. data/clients/{id}/history.json 세션 추가
    2. logs/processed.json 타임스탬프 등록
    """
    client_id = args.client
    client_dir = get_client_dir(client_id)
    if not client_dir.exists():
        msg = f"finalize 실패: {client_id} 디렉토리 없음"
        print(f"[ERROR] {msg}")
        log_error(msg, client_id)
        return

    divider()
    print(f"[FINALIZE] {client_id}")
    divider()

    next_review = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

    # ── 고객 이메일 (processed 조합키 중복방지 + 하단 발송 안내 공용) ──
    kyc_path = client_dir / "kyc.json"
    client_email = ""
    if kyc_path.exists():
        try:
            kyc = json.loads(kyc_path.read_text(encoding="utf-8"))
            client_email = (kyc.get("profile", {}).get("email") or "").strip().lower()
        except Exception as e:
            print(f"  [경고] kyc.json 이메일 읽기 실패 (이메일 미등록 처리): {e}")

    # ── history.json ──
    history_path = client_dir / "history.json"
    history = {"client_id": client_id, "sessions": []}
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [경고] history.json 읽기 실패 — 기존 세션이 손실될 수 있습니다: {e}")
            print(f"         파일 확인 후 수동 복구하세요: {history_path}")

    # ── recommended_actions: 다음 재진단 이행 추적용 구조화 필드 ──
    recommended_actions: dict = {}
    score_detail: dict = {}
    risk_path = client_dir / "risk_score.json"
    if risk_path.exists():
        try:
            risk_data = json.loads(risk_path.read_text(encoding="utf-8"))
            recommended_actions = risk_data.get("recommended_actions") or {}
            score_detail = {
                k: v.get("score")
                for k, v in (risk_data.get("details") or {}).items()
                if isinstance(v, dict) and "score" in v
            }
        except Exception as e:
            print(f"  [경고] risk_score.json 읽기 실패: {e}")

    # ── trending_snapshot: 추천 시점의 종목 가격 저장 (사후 성과 추적용) ──
    trending_snapshot = _extract_trending_snapshot(client_dir)

    new_session = {
        "date":               TODAY,
        "datetime":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_timestamp":   args.timestamp,
        "report_md":          f"data/clients/{client_id}/reports/{TODAY}.md",
        "report_html":        f"data/clients/{client_id}/reports/{TODAY}.html",
        "total_score":        args.score,
        "grade":              args.grade,
        "risk_type":          args.risk_type,
        "weakest_point":      args.weakest,
        "score_detail":       score_detail,
        "verdict":             args.verdict,
        "retry_count":         0,
        "next_review_date":    next_review,
        "trending_snapshot":   trending_snapshot,
        "recommended_actions": recommended_actions,
    }
    # 같은 설문 응답(source_timestamp)을 재처리한 경우만 덮어씀.
    # 오후에 새 설문으로 재진단하면 timestamp 가 달라 아침 세션이 보존된다.
    # (구버전 세션은 source_timestamp 가 없어 그대로 보존됨)
    history["sessions"] = [
        s for s in history["sessions"] if s.get("source_timestamp") != args.timestamp
    ]
    history["sessions"].append(new_session)
    safe_write_json(history_path, history)
    print(f"  ✅ history.json 업데이트 ({TODAY} 세션 추가)")

    # ── processed.json ──
    processed = load_processed()
    # 중복 판정은 (timestamp, email) 조합 — 같은 초에 다른 사용자가 제출해도 구분
    already = any(
        p.get("timestamp") == args.timestamp
        and (p.get("email") or "").strip().lower() == client_email
        for p in processed
    )
    if already:
        print(f"  ⚠️  processed.json: '{args.timestamp}' (이메일 {client_email or 'N/A'}) 이미 등록됨 (중복 스킵)")
    else:
        processed.append({
            "timestamp":    args.timestamp,
            "email":        client_email,
            "client_id":    client_id,
            "processed_at": TODAY,
            "verdict":      args.verdict,
            "total_score":  args.score,
            "grade":        args.grade,
        })
        save_processed(processed)
        print(f"  ✅ processed.json 업데이트 ('{args.timestamp}' 등록)")

    # ── DLQ 정리 (이전에 실패 적재된 고객이면 최종 성공으로 제거) ──
    if clear_dlq(client_id):
        print(f"  ✅ DLQ에서 {client_id} 제거 (최종 성공)")

    # ── 이메일 발송 안내 (client_email 은 위에서 이미 추출됨) ──
    divider()
    print(f"  🎉 [{client_id}] 파이프라인 완료!")
    print(f"  점수: {args.score}점 {args.grade}  |  성향: {args.risk_type}  |  최약점: {args.weakest}")
    print(f"  다음 진단 권고: {next_review}")
    if client_email:
        print(f"\n  📧 리포트 발송 가능:")
        print(f"     python scripts/orchestrator.py send --client {client_id} --to {client_email}")
    else:
        print(f"\n  ℹ️  이메일 미등록 — 발송하려면 kyc.json에 profile.email을 추가하거나")
        print(f"     --to 옵션으로 직접 지정하세요:")
        print(f"     python scripts/orchestrator.py send --client {client_id} --to 고객이메일@example.com")
    divider()


# ═════════════════════════════════════
# 명령: send
# ═════════════════════════════════════

def cmd_send(args):
    """완성된 리포트(.html)를 고객 이메일로 발송. send_report.py에 위임."""
    client_id  = args.client
    client_dir = get_client_dir(client_id)
    if not client_dir.exists():
        print(f"[ERROR] 클라이언트 디렉토리 없음: {client_id}")
        return

    # 수신자 이메일 확인 (--to 우선, 없으면 kyc.json)
    to_email = (args.to or "").strip()
    if not to_email:
        kyc_path = client_dir / "kyc.json"
        if kyc_path.exists():
            try:
                kyc = json.loads(kyc_path.read_text(encoding="utf-8"))
                to_email = (kyc.get("profile", {}).get("email") or "").strip()
            except Exception:
                pass
    if not to_email:
        print(f"[ERROR] 수신자 이메일을 찾을 수 없습니다.")
        print(f"  → --to 옵션으로 직접 지정하거나 kyc.json profile.email을 채우세요.")
        return

    # 리포트 날짜
    report_date = (args.date or "").strip() or TODAY
    ok = run_script("send_report.py", client_id, to_email, report_date)
    if ok:
        print(f"  ✅ 발송 완료 → {to_email}")
    else:
        print(f"  ❌ 발송 실패 — logs/errors.log 확인")


# ═════════════════════════════════════
# 명령: dlq
# ═════════════════════════════════════

def cmd_dlq(args):
    """Dead Letter Queue(실패 고객) 조회/정리."""
    items = _load_dlq()
    if getattr(args, "clear", False):
        if args.client:
            removed = clear_dlq(args.client)
            print(f"  DLQ {args.client}: {'제거됨' if removed else '해당 항목 없음'}")
        else:
            _save_dlq([])
            print(f"  DLQ 전체 비움 ({len(items)}건 제거)")
        return
    divider()
    print(f"[DLQ] 실패 고객 {len(items)}건")
    divider()
    if not items:
        print("  ✅ 적재된 실패 고객 없음")
        return
    for it in items:
        print(f"  • {it.get('client_id')} | 재시도 {it.get('retry_count', 0)}회 "
              f"| 마지막 단계: {it.get('last_stage', '?')} | {it.get('updated_at', '')}")
        print(f"      사유: {it.get('last_reason', '')}")


# ═════════════════════════════════════
# main — CLI 라우팅
# ═════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="자산관리 파이프라인 반자동화 보조 도구 (Method B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="자세한 사용법: CLAUDE.md 파이프라인 상세 실행 순서 참조"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # prepare
    sub.add_parser("prepare", help="미처리 고객 파악 + 0-A/0-B 스크립트 실행")

    # validate
    p_val = sub.add_parser("validate", help="에이전트 출력 Pydantic 검증")
    p_val.add_argument(
        "--agent", required=True,
        choices=["kyc", "correlation", "portfolio", "stock", "risk", "reviewer", "macro", "report"],
        help="검증할 에이전트 이름"
    )
    p_val.add_argument("--client", default="", help="client_id (macro 제외 필수)")
    p_val.add_argument("--date", default="",
                       help="agent=report 검증 시 리포트 날짜 YYYY-MM-DD (생략 시 최신 .md)")

    # normalize
    p_norm = sub.add_parser("normalize", help="ticker_normalizer.py 실행")
    p_norm.add_argument("--client", required=True)

    # correlate
    p_corr = sub.add_parser("correlate", help="correlation_analyzer.py 실행 (상관계수 분석)")
    p_corr.add_argument("--client", required=True)

    # macro-check
    sub.add_parser("macro-check", help="macro_snapshot.json 날짜 유효성 확인")

    # status
    p_stat = sub.add_parser("status", help="파이프라인 진행 상태 확인")
    p_stat.add_argument("--client", default=None, help="생략 시 전체 고객 현황 출력")

    # finalize
    p_fin = sub.add_parser("finalize", help="history.json + processed.json 자동 업데이트")
    p_fin.add_argument("--client",    required=True)
    p_fin.add_argument("--score",     required=True, type=int, help="종합 점수 (0~100)")
    p_fin.add_argument("--grade",     required=True, help="등급 이모지 (🟢/🟡/🔴)")
    p_fin.add_argument("--risk_type", required=True, help="투자 성향 (안정형/중립형/적극형)")
    p_fin.add_argument("--weakest",   required=True,
                       help="최약 지표 (cashflow/behavioral_gap/emergency_fund/diversification)")
    p_fin.add_argument("--verdict",   required=True,
                       choices=["PASS", "PASS WITH WARNING", "FAIL"])
    p_fin.add_argument("--timestamp", required=True,
                       help="responses.csv 타임스탬프 (예: '2026/05/28 09:15:00')")

    # send
    p_send = sub.add_parser("send", help="완성된 리포트를 고객 이메일로 발송")
    p_send.add_argument("--client", required=True, help="client_id")
    p_send.add_argument("--to", default=None, help="수신자 이메일 (미지정 시 kyc.json의 profile.email 사용)")
    p_send.add_argument("--date", default=None,
                        help="발송할 리포트 날짜 YYYY-MM-DD (미지정 시 오늘)")

    # dlq
    p_dlq = sub.add_parser("dlq", help="Dead Letter Queue(실패 고객) 조회/정리")
    p_dlq.add_argument("--client", default=None, help="특정 client_id (--clear와 함께 개별 제거)")
    p_dlq.add_argument("--clear", action="store_true", help="DLQ 비우기 (--client 없으면 전체)")

    # screen
    p_screen = sub.add_parser("screen", help="value_screener.py 실행 → value_picks.json 생성")
    p_screen.add_argument("--force", action="store_true", help="오늘 캐시가 있어도 강제 재실행")

    # backtest
    p_bt = sub.add_parser("backtest", help="backtester.py 실행 → 추천 종목 성과 추적")
    p_bt.add_argument("--client", default=None, help="특정 고객만 (생략 시 전체)")

    # agent (LLM 에이전트 호출)
    p_agent = sub.add_parser("agent", help="LLM 에이전트 호출 및 결과 저장 (call_agent_with_validation 사용)")
    p_agent.add_argument("--name", required=True, help="에이전트 이름 (kyc, portfolio, stock, risk, reviewer, correlation, macro)")
    p_agent.add_argument("--client", default=None, help="client_id (macro 제외 필수)")

    args = parser.parse_args()

    dispatch = {
        "prepare":     cmd_prepare,
        "validate":    cmd_validate,
        "normalize":   cmd_normalize,
        "correlate":   cmd_correlate,
        "macro-check": cmd_macro_check,
        "status":      cmd_status,
        "finalize":    cmd_finalize,
        "send":        cmd_send,
        "dlq":         cmd_dlq,
        "screen":      cmd_screen,
        "backtest":    cmd_backtest,
        "agent":       cmd_agent,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


# ═════════════════════════════════════
# 하위 호환: 기존 LLM 클라이언트 코드
# (Phase 5-B 완전 자동화 시 사용 예정)
# ═════════════════════════════════════

def extract_json_from_response(text: str) -> str:
    """LLM 응답에서 ```json ... ``` 마크다운 블록 제거 후 순수 JSON 반환"""
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    return match.group(1).strip() if match else text.strip()


def call_agent_with_validation(agent_name: str, prompt: str, schema_class, llm_client,
                                max_retries: int = 3):
    """LLM 호출 → Pydantic 검증 → 자가 수정 루프 (최대 max_retries회)"""
    current_prompt = prompt
    for attempt in range(1, max_retries + 1):
        try:
            raw     = llm_client.generate(current_prompt)
            parsed  = json.loads(extract_json_from_response(raw))
            validated = schema_class.model_validate(parsed)
            print(f"[{agent_name}] ✅ Attempt {attempt} — Pydantic 통과")
            return validated.model_dump()
        except json.JSONDecodeError:
            error_msg = "Output is not valid JSON. Return ONLY a valid JSON object."
        except ValidationError as e:
            error_msg = f"Validation errors:\n{e.json()}"
            print(f"[{agent_name}] ❌ Attempt {attempt} — {e.error_count()}개 오류")
        current_prompt = (
            f"{prompt}\n\n[SYSTEM FEEDBACK]\n{error_msg}\n"
            "위 오류를 수정하여 올바른 JSON을 반환하세요."
        )
    raise Exception(f"[{agent_name}] {max_retries}회 재시도 후에도 실패.")


# ═════════════════════════════════════
# 명령: agent (LLM 에이전트 호출)
# ═════════════════════════════════════

def cmd_agent(args):
    """LLM 에이전트를 호출하여 Pydantic 검증을 통과한 JSON을 파일로 저장합니다.
    사용 예:
      python scripts/orchestrator.py agent --name kyc --client client_20260531_001
      python scripts/orchestrator.py agent --name macro
    """
    agent_name = args.name.lower()
    client_id = getattr(args, "client", None)

    # 파일/스키마 결정
    if agent_name == "macro":
        filename = MACRO_SNAPSHOT
        schema = MacroAnalystOutput if PYDANTIC_AVAILABLE else None
    else:
        if agent_name not in AGENT_FILE_MAP:
            print(f"[ERROR] 알 수 없는 에이전트: {agent_name}")
            return
        fname, schema = AGENT_FILE_MAP[agent_name]
        if not client_id:
            print(f"[ERROR] --client 필수 (agent={agent_name})")
            return
        filename = get_client_dir(client_id) / fname

    # 프롬프트 템플릿 로드 (.claude/agents/{agent}.md 또는 scripts/agent_prompts/{agent}.md)
    prompt = None
    agent_md = BASE_DIR / ".claude" / "agents" / f"{agent_name}.md"
    prompt_dir = SCRIPTS_DIR / "agent_prompts"
    prompt_md2 = prompt_dir / f"{agent_name}.md"
    if agent_md.exists():
        try:
            prompt = agent_md.read_text(encoding="utf-8")
        except Exception:
            prompt = None
    elif prompt_md2.exists():
        try:
            prompt = prompt_md2.read_text(encoding="utf-8")
        except Exception:
            prompt = None

    if not prompt:
        prompt = (
            f"You are the `{agent_name}` agent. Return ONLY a single JSON object that conforms to the required schema."
        )

    # LLM 클라이언트 확인
    if LLMClient is None:
        print("[ERROR] LLM client (scripts/llm_client.py) 설치/사용 불가. 파일을 확인하세요.")
        return

    client = LLMClient()

    try:
        result = call_agent_with_validation(agent_name, prompt, schema, client)
    except Exception as e:
        msg = f"에이전트 호출 실패: {e}"
        print(f"  ❌ {msg}")
        log_error(msg, f"{client_id}/{agent_name}" if client_id else agent_name)
        return

    # 결과 저장 (safe_write_json으로 원자적 쓰기 — 중단 시 파일 손상 방지)
    try:
        safe_write_json(filename, result)
        print(f"  ✅ {filename}에 결과 저장됨")
        if agent_name != "macro":
            print(f"  ※ 저장 후: python scripts/orchestrator.py validate --agent {agent_name} --client {client_id}")
        else:
            print(f"  ※ 저장 후: python scripts/orchestrator.py validate --agent macro")
    except Exception as e:
        msg = f"결과 저장 실패: {e}"
        print(f"  ❌ {msg}")
        log_error(msg, f"{client_id}/{agent_name}" if client_id else agent_name)



if __name__ == "__main__":
    main()
