"""
Google Sheets → responses.csv 동기화 스크립트

사용법:
  python scripts/sync_forms.py
  python scripts/sync_forms.py --manual-test   # 시트 없이 테스트 응답 1건 추가

필요 패키지:
  pip install gspread google-auth

사전 준비:
  1. Google Cloud Console에서 서비스 계정 생성
  2. credentials.json을 프로젝트 최상단 폴더에 저장 (CLAUDE.md와 같은 위치)
  3. SPREADSHEET_ID를 아래에 입력
  4. 응답 시트를 서비스 계정 이메일(client_email)과 '뷰어'로 공유

동작 원칙 (v5 폼 대응):
  - 시트의 '열 이름'을 기준으로 매핑한다 (열 순서가 바뀌거나 새 열이 추가돼도 안전).
  - 타임스탬프를 'YYYY/MM/DD HH:MM:SS'로 정규화한다 (구글폼 한국 로케일 '오전/오후' 지원).
  - responses.csv를 캐노니컬 헤더(CSV_HEADER)로 매 동기화 시 재작성한다
    (옛 헤더 자동 마이그레이션 + 원자적 쓰기).
"""

import csv
import os
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
RESPONSES_CSV = DATA_DIR / "responses.csv"

# 환경변수 SPREADSHEET_ID 또는 .env 파일로 오버라이드 가능.
# 예: .env 파일에 SPREADSHEET_ID=1abc...xyz 추가
SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID",
    "1a_VK4d9n1w8a0RTBj6HZEDlT6Td7hFMGIJJBQ_0cjT0",  # 기본값 — 환경변수로 덮어쓸 수 있음
)
SHEET_NAME = os.environ.get("SHEET_NAME", "설문지 응답 시트1")

CREDENTIALS_FILE = BASE_DIR / "credentials.json"

# responses.csv 캐노니컬 컬럼 (CSV v5.3 — 25컬럼, Google Sheets 헤더 순서 기준)
# orchestrator는 DictReader(헤더 키 기준)로 읽으므로 '이름'만 맞으면 순서는 무관하지만,
# 가독성을 위해 시트 헤더 순서를 따른다. 시트의 '자산첨부파일'(파일 업로드 링크)은
# 분석에 쓰지 않으므로 제외한다.
CSV_HEADER = [
    "타임스탬프", "이메일", "연령대", "직업형태", "부양가족여부", "투자경험여부",
    "투자목표", "하락시대처", "적립식활용여부", "월수입", "월여유자금",
    "현금자산", "비유동성자산", "저금리부채", "고금리부채", "연금자산",
    "투자자산", "세금계좌", "ESG제외업종", "목돈투자희망금액", "목표기간",
    "목표금액", "대출금리", "보장성보험", "예상연금월액", "기타메모"
]
# 목돈투자희망금액: 현재 보유 현금 중 주식·ETF에 일시 투자하고 싶은 금액 (만원 단위).
# 빈칸이면 0 처리. portfolio-designer가 비상금 안전선 계산 후 실제 투자 가능 금액을 산출.
# 목표기간: 목표 달성까지 남은 기간 (예: "5년", "10년 후", "20년"). kyc-collector가 숫자 파싱.
# [v5.3 추가 4종] 목표금액: 목표 달성에 필요한 총액(만원) → Funded Ratio. 대출금리: 보유 대출 실금리(%).
# 보장성보험: 예/아니오 → insurance_gap(V29). 예상연금월액: 은퇴 후 월 연금(만원) → 은퇴 인출 설계.
# 컬럼이 없거나 빈칸이면 kyc-collector가 null 처리 후 계속 진행(하위호환).

# 구글폼 이메일 컬럼 후보명 (설정 → '이메일 주소 수집' 시 자동 생성되는 컬럼명)
_EMAIL_COL_CANDIDATES = ["이메일", "이메일 주소", "Email", "email"]


def normalize_timestamp(ts: str) -> str:
    """구글폼 타임스탬프를 'YYYY/MM/DD HH:MM:SS'로 정규화.

    - 이미 정규형이면 그대로 반환.
    - 한국 로케일 '2026. 5. 30 오전 10:47:35' (오전/오후) 지원.
    - 변환 실패 시 원본 반환(prepare가 경고 후 스킵).
    """
    ts = (ts or "").strip()
    if not ts:
        return ts

    # 이미 정규형?
    try:
        datetime.strptime(ts, "%Y/%m/%d %H:%M:%S")
        return ts
    except ValueError:
        pass

    # 한국 로케일: "2026. 5. 30 오전 10:47:35" / "오후 1:05:00"
    m = re.match(
        r"\s*(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*"
        r"(오전|오후)?\s*(\d{1,2})\s*:\s*(\d{1,2})\s*:\s*(\d{1,2})",
        ts,
    )
    if m:
        y, mo, d, ampm, h, mi, s = m.groups()
        h = int(h)
        if ampm == "오후" and h != 12:
            h += 12
        elif ampm == "오전" and h == 12:
            h = 0
        return f"{int(y):04d}/{int(mo):02d}/{int(d):02d} {h:02d}:{int(mi):02d}:{int(s):02d}"

    # 기타 형식 폴백
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y. %m. %d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).strftime("%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
    return ts  # 변환 실패 → 원본 유지


def _detect_csv_encoding(path: Path) -> str:
    """CP949/EUC-KR로 저장된 기존 CSV를 안전하게 읽기 위한 인코딩 자동 감지.
    orchestrator.py의 감지 로직과 동일 순서 적용.
    """
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            with open(path, newline="", encoding=enc) as f:
                f.read(512)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def load_existing_responses() -> list:
    """현재 responses.csv를 dict 리스트로 로드(헤더 이름 기준 — 옛 헤더와도 호환).
    인코딩 자동 감지: CP949/EUC-KR로 저장된 기존 파일도 정상 처리.
    """
    if not RESPONSES_CSV.exists():
        return []
    enc = _detect_csv_encoding(RESPONSES_CSV)
    with open(RESPONSES_CSV, newline="", encoding=enc) as f:
        return list(csv.DictReader(f))


def write_all_responses(all_dicts: list) -> None:
    """responses.csv를 캐노니컬 헤더로 재작성(이름 기준 매핑, 원자적 쓰기)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RESPONSES_CSV.with_name(RESPONSES_CSV.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for d in all_dicts:
            writer.writerow([(d.get(col, "") or "") for col in CSV_HEADER])
    os.replace(tmp, RESPONSES_CSV)


def sync_from_sheets():
    """Google Sheets에서 최신 응답을 받아 responses.csv 업데이트(이름 기준 매핑)."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        rows = sheet.get_all_values()

        if not rows:
            print("시트가 비어있습니다.")
            return 0

        sheet_header = [h.strip() for h in rows[0]]
        data_rows = rows[1:]

        existing = load_existing_responses()
        existing_ts = {normalize_timestamp(r.get("타임스탬프", "")) for r in existing}

        new_dicts = []
        for r in data_rows:
            d = {sheet_header[i]: (r[i] if i < len(r) else "") for i in range(len(sheet_header))}

            # 이메일 컬럼 정규화: 구글폼 자동수집 '이메일 주소' → '이메일'로 통일
            if not d.get("이메일"):
                for cand in _EMAIL_COL_CANDIDATES:
                    if d.get(cand):
                        d["이메일"] = d[cand].strip().lower()
                        break

            # Google Form v5 질문명 변경 대응: '투자자산(직접입력)' -> '투자자산' 매핑
            if "투자자산(직접입력)" in d and not d.get("투자자산"):
                d["투자자산"] = d["투자자산(직접입력)"]
                
            ts_norm = normalize_timestamp(d.get("타임스탬프", ""))
            if not ts_norm or ts_norm in existing_ts:
                continue
            d["타임스탬프"] = ts_norm        # 정규화된 타임스탬프로 교체
            existing_ts.add(ts_norm)         # 같은 동기화 내 중복 방지
            new_dicts.append(d)

        if not new_dicts:
            print("새로운 응답이 없습니다.")
            return 0

        # 기존 + 신규를 캐노니컬 헤더로 재작성(옛 헤더 자동 마이그레이션 포함)
        write_all_responses(existing + new_dicts)
        print(f"새 응답 {len(new_dicts)}건 추가됨.")
        return len(new_dicts)

    except ImportError:
        print("[오류] gspread 패키지가 없습니다: pip install gspread google-auth")
        return 0
    except FileNotFoundError:
        print(f"[오류] credentials.json이 없습니다: {CREDENTIALS_FILE}")
        return 0
    except Exception as e:
        print(f"[오류] Sheets 동기화 실패: {e}")
        return 0


def add_manual_entry(data: dict):
    """수동으로 응답 1건 추가 (테스트용). 키는 CSV_HEADER(타임스탬프 제외)와 대응한다."""
    timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    row = {"타임스탬프": timestamp}
    for col in CSV_HEADER:
        if col == "타임스탬프":
            continue
        row[col] = data.get(col, "")
    existing = load_existing_responses()
    write_all_responses(existing + [row])
    print(f"수동 항목 추가: {timestamp}")
    return timestamp


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--manual-test":
        add_manual_entry({
            "연령대": "30대",
            "직업형태": "자영업/프리랜서",
            "부양가족여부": "예",
            "투자경험여부": "아니오",
            "투자목표": "5년 내 1억 모으기",
            "하락시대처": "③ 추가 매수 기회로 삼아 자산을 더 사하겠다 (적극형)",
            "적립식활용여부": "아니오",
            "월수입": "350",
            "월여유자금": "100",
            "현금자산": "500",
            "비유동성자산": "30000",
            "저금리부채": "5000",
            "고금리부채": "0",
            "연금자산": "300",
            "투자자산": "신라젠 600, 초전도체테마 200, 삼성전자 200",
            "세금계좌": "일반계좌",
            "ESG제외업종": "",
            "기타메모": "테스트 데이터",
        })
    else:
        sync_from_sheets()
