import os
import json
import uuid
import io
import csv
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google import genai
from google.genai import types

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
    from google.oauth2.service_account import Credentials as SACredentials
    DRIVE_AVAILABLE = True
except ImportError:
    DRIVE_AVAILABLE = False

# ── 환경변수 ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SERVICE_ACCOUNT_JSON_STR = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
RESPONSES_CSV_FILE_ID = os.environ.get("GOOGLE_DRIVE_RESPONSES_CSV_FILE_ID", "")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ── FastAPI 앱 ────────────────────────────────────────────────────────────────
app = FastAPI(title="재무 상담 인터뷰")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 인메모리 세션 저장소 ───────────────────────────────────────────────────────
sessions: Dict[str, dict] = {}

# ── 인터뷰 시스템 프롬프트 ────────────────────────────────────────────────────
INTERVIEW_SYSTEM_PROMPT = """당신은 따뜻하고 친근한 재무 상담 AI 인터뷰어입니다.
고객과 자연스러운 대화를 통해 아래 정보를 수집해야 합니다.

## 수집해야 할 정보 (22가지)

1. 이메일 주소
2. 연령대 (20대/30대/40대/50대/60대 이상)
3. 직업형태 (급여소득자/자영업·프리랜서/전업주부·무직/은퇴)
4. 부양가족 여부 (예/아니오)
5. 투자 경험 여부 (예/아니오)
6. 투자 목표 (예: 주택 구입, 은퇴 자산 마련, 결혼 자금, 자녀 교육비 등)
7. 하락 시 대처 방식 (반드시 아래 3가지 중 하나로 유도하세요)
   - "1. 팔고 싶다 (손실이라도 손절하고 현금 확보)"
   - "2. 일부만 팔아 손실을 줄인다 (보유 지속)"
   - "3. 더 사겠다 (하락이 기회라고 생각)"
8. 적립식 투자 활용 여부 (예/아니오)
9. 월 수입 (만원 단위)
10. 월 여유자금 (만원 단위 — 수입에서 지출 후 남는 금액)
11. 현금성 자산 (만원 단위 — 예·적금, CMA, MMF 등)
12. 비유동성 자산 (만원 단위 — 부동산, 자동차 등)
13. 저금리 부채 (만원 단위 — 주택담보대출, 전세자금대출 등)
14. 고금리 부채 (만원 단위 — 신용대출, 카드론, 마이너스통장 등)
15. 연금 자산 (만원 단위 — 국민연금 제외, IRP·연금저축 누적액)
16. 투자 자산 (주식, ETF 등 — 종목명[계좌종류] 금액 형식으로, 예: "삼성전자[ISA] 200")
17. 세금 혜택 계좌 종류 (ISA/IRP/연금저축 중 보유한 것)
18. ESG 제외 업종 (예: 담배·주류, 화석연료, 방산 등 — 없으면 빈칸)
19. 목돈 투자 희망 금액 (만원 단위 — 없으면 0)
20. 투자 목표 기간 (예: 3년, 5년, 10년, 20년)
21. 기타 메모 (특이사항, 향후 계획 등)

## 대화 진행 규칙

- 한 번에 1~2가지 질문만 합니다.
- 금액을 물을 때는 반드시 "만원 단위로" 명시합니다.
- 고객이 모른다거나 없다고 하면 0 또는 없음으로 처리합니다.
- 자연스럽게 공감하며 대화합니다.
- 전문 용어 사용을 최소화하고, 쉬운 말로 설명합니다.

## 특별 규칙

**하락 시 대처 질문 시:**
투자 성향을 물어볼 때는 반드시 응답 끝에 `[SHOW_RISK_BUTTONS]` 마커를 추가하세요.
예시: "주식이 30% 떨어지면 어떻게 하시겠어요?\n[SHOW_RISK_BUTTONS]"

**모든 정보 수집 완료 시:**
모든 22가지 정보를 수집했다고 판단되면, 수집된 내용을 간단히 요약해주고 응답 끝에 `[COMPLETE]` 마커를 추가하세요.
예시: "지금까지 말씀해주신 내용을 바탕으로 맞춤 진단을 시작할 준비가 됐어요! ...(요약)... [COMPLETE]"

## 시작 인사
첫 응답에서는 자신을 소개하고 이메일 주소부터 편하게 물어보세요."""

# ── 데이터 추출 프롬프트 ──────────────────────────────────────────────────────
EXTRACTION_PROMPT_TEMPLATE = """아래는 재무 상담 인터뷰 대화 내용입니다.
이 대화에서 수집된 정보를 JSON 형식으로 추출해주세요.

대화 내용:
{conversation}

---

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "이메일": "이메일 주소 또는 빈 문자열",
  "연령대": "20대/30대/40대/50대/60대 이상 중 하나",
  "직업형태": "급여소득자/자영업·프리랜서/전업주부·무직/은퇴 중 하나",
  "부양가족여부": "예 또는 아니오",
  "투자경험여부": "예 또는 아니오",
  "투자목표": "투자 목표 텍스트",
  "하락시대처": "반드시 아래 셋 중 하나: '1. 팔고 싶다 (손실이라도 손절하고 현금 확보)' 또는 '2. 일부만 팔아 손실을 줄인다 (보유 지속)' 또는 '3. 더 사겠다 (하락이 기회라고 생각)'",
  "적립식활용여부": "예 또는 아니오",
  "월수입": "숫자 문자열 (만원 단위, 예: '450')",
  "월여유자금": "숫자 문자열 (만원 단위)",
  "현금자산": "숫자 문자열 (만원 단위)",
  "비유동성자산": "숫자 문자열 (만원 단위)",
  "저금리부채": "숫자 문자열 (만원 단위)",
  "고금리부채": "숫자 문자열 (만원 단위)",
  "연금자산": "숫자 문자열 (만원 단위)",
  "투자자산": "종목명[계좌] 금액 형식의 쉼표 구분 문자열, 예: '삼성전자[ISA] 200, KODEX200[연금저축] 100'",
  "세금계좌": "ISA/IRP/연금저축 중 보유한 것들, 쉼표 구분 또는 빈 문자열",
  "ESG제외업종": "제외 업종 또는 빈 문자열",
  "목돈투자희망금액": "숫자 문자열 (만원 단위, 없으면 '0')",
  "목표기간": "기간 텍스트 (예: '5년')",
  "기타메모": "기타 특이사항 텍스트 또는 빈 문자열"
}}

주의사항:
- 모든 금액은 만원 단위 정수 문자열
- 언급되지 않은 항목은 빈 문자열 또는 '0'
- 하락시대처는 반드시 위 세 가지 중 하나를 그대로 사용"""

# ── CSV 헤더 (responses.csv와 동일) ──────────────────────────────────────────
CSV_HEADER = [
    "타임스탬프", "이메일", "연령대", "직업형태", "부양가족여부", "투자경험여부",
    "투자목표", "하락시대처", "적립식활용여부", "월수입", "월여유자금", "현금자산",
    "비유동성자산", "저금리부채", "고금리부채", "연금자산", "투자자산", "세금계좌",
    "ESG제외업종", "목돈투자희망금액", "목표기간", "기타메모"
]

# ── Pydantic 모델 ─────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    message: str

class ChatResponse(BaseModel):
    message: str
    show_risk_buttons: bool = False
    show_complete_button: bool = False

class SessionResponse(BaseModel):
    session_id: str
    message: str

# ── Google Drive 연동 ─────────────────────────────────────────────────────────
def _get_drive_service():
    if not DRIVE_AVAILABLE or not SERVICE_ACCOUNT_JSON_STR:
        return None
    try:
        creds_info = json.loads(SERVICE_ACCOUNT_JSON_STR)
        creds = SACredentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"[Drive] 인증 실패: {e}")
        return None


def append_row_to_drive_csv(row: List[str]) -> bool:
    """Google Drive의 responses.csv에 행 추가 후 재업로드."""
    service = _get_drive_service()
    if not service or not RESPONSES_CSV_FILE_ID:
        return False

    try:
        # 기존 CSV 다운로드
        request = service.files().get_media(fileId=RESPONSES_CSV_FILE_ID)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        existing_content = buf.getvalue().decode("utf-8-sig")
        lines = existing_content.splitlines()

        # 헤더가 없으면 추가
        if not lines or lines[0].strip() == "":
            lines = [",".join(CSV_HEADER)]

        # 새 행 추가
        out_buf = io.StringIO()
        writer = csv.writer(out_buf, quoting=csv.QUOTE_MINIMAL)
        for line in lines:
            out_buf.write(line + "\n")
        writer.writerow(row)

        # 업로드
        upload_buf = io.BytesIO(out_buf.getvalue().encode("utf-8-sig"))
        media = MediaIoBaseUpload(upload_buf, mimetype="text/csv", resumable=False)
        service.files().update(fileId=RESPONSES_CSV_FILE_ID, media_body=media).execute()
        print(f"[Drive] CSV 업데이트 완료: {row[0]}")
        return True

    except Exception as e:
        print(f"[Drive] 업로드 실패: {e}")
        return False


def save_to_local_fallback(row: List[str]) -> None:
    """Drive 실패 시 로컬 대기 파일에 저장."""
    pending_path = Path(__file__).parent.parent / "data" / "interview_pending.csv"
    pending_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not pending_path.exists()
    with open(pending_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(CSV_HEADER)
        writer.writerow(row)
    print(f"[Fallback] 로컬 저장: {pending_path}")


# ── 대화 텍스트 구성 ──────────────────────────────────────────────────────────
def _build_conversation_text(history: List[dict]) -> str:
    lines = []
    for turn in history:
        role = "AI" if turn["role"] == "model" else "고객"
        text = turn["parts"][0] if isinstance(turn["parts"][0], str) else turn["parts"][0].get("text", "")
        # 마커 제거
        text = text.replace("[SHOW_RISK_BUTTONS]", "").replace("[COMPLETE]", "").strip()
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


# ── API 엔드포인트 ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def get_interview():
    html_path = Path(__file__).parent.parent / "templates" / "interview.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>interview.html 파일을 찾을 수 없습니다.</h1>", status_code=404)


@app.post("/session/start", response_model=SessionResponse)
async def start_session():
    if not client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY가 설정되지 않았습니다.")

    session_id = str(uuid.uuid4())

    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(system_instruction=INTERVIEW_SYSTEM_PROMPT),
    )
    greeting = chat.send_message("인터뷰를 시작해주세요.")
    greeting_text = greeting.text

    sessions[session_id] = {
        "chat": chat,
        "history": [
            {"role": "model", "parts": [greeting_text]}
        ],
        "completed": False,
    }

    return SessionResponse(session_id=session_id, message=greeting_text)


@app.post("/chat/{session_id}", response_model=ChatResponse)
async def chat_endpoint(session_id: str, body: ChatMessage):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    session = sessions[session_id]
    if session["completed"]:
        raise HTTPException(status_code=400, detail="이미 완료된 세션입니다.")

    chat = session["chat"]
    session["history"].append({"role": "user", "parts": [body.message]})

    try:
        response = chat.send_message(body.message)
        ai_text = response.text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini 오류: {str(e)}")

    session["history"].append({"role": "model", "parts": [ai_text]})

    show_risk_buttons = "[SHOW_RISK_BUTTONS]" in ai_text
    show_complete_button = "[COMPLETE]" in ai_text

    clean_text = ai_text.replace("[SHOW_RISK_BUTTONS]", "").replace("[COMPLETE]", "").strip()

    return ChatResponse(
        message=clean_text,
        show_risk_buttons=show_risk_buttons,
        show_complete_button=show_complete_button,
    )


@app.post("/complete/{session_id}")
async def complete_interview(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    session = sessions[session_id]
    if session["completed"]:
        return {"status": "already_completed"}

    # 대화 내용 → JSON 추출
    conversation_text = _build_conversation_text(session["history"])
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(conversation=conversation_text)

    try:
        extraction_response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_json = extraction_response.text.strip()

        # JSON 블록 파싱
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        extracted = json.loads(raw_json)

    except Exception as e:
        print(f"[Extract] JSON 추출 실패: {e}")
        extracted = {}

    # CSV 행 구성 (타임스탬프 포함 22컬럼)
    timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    row = [
        timestamp,
        extracted.get("이메일", ""),
        extracted.get("연령대", ""),
        extracted.get("직업형태", ""),
        extracted.get("부양가족여부", ""),
        extracted.get("투자경험여부", ""),
        extracted.get("투자목표", ""),
        extracted.get("하락시대처", ""),
        extracted.get("적립식활용여부", ""),
        extracted.get("월수입", "0"),
        extracted.get("월여유자금", "0"),
        extracted.get("현금자산", "0"),
        extracted.get("비유동성자산", "0"),
        extracted.get("저금리부채", "0"),
        extracted.get("고금리부채", "0"),
        extracted.get("연금자산", "0"),
        extracted.get("투자자산", ""),
        extracted.get("세금계좌", ""),
        extracted.get("ESG제외업종", ""),
        extracted.get("목돈투자희망금액", "0"),
        extracted.get("목표기간", ""),
        extracted.get("기타메모", ""),
    ]

    # Drive 업로드 시도 → 실패 시 로컬 저장
    drive_success = append_row_to_drive_csv(row)
    if not drive_success:
        save_to_local_fallback(row)

    session["completed"] = True

    return {
        "status": "success",
        "drive_saved": drive_success,
        "extracted": extracted,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "drive_available": DRIVE_AVAILABLE and bool(SERVICE_ACCOUNT_JSON_STR),
        "gemini_configured": bool(GEMINI_API_KEY),
    }
