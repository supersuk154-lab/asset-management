"""
send_report.py — 완성된 리포트를 고객 이메일로 발송

사용법 (orchestrator가 호출):
  python scripts/send_report.py <client_id> <to_email> <report_date>

직접 테스트:
  python scripts/send_report.py client_20260530_001 test@example.com 2026-05-30

필요 설정 (최초 1회):
  환경 변수 또는 .env 파일로 설정:
    GMAIL_USER=보내는Gmail주소@gmail.com
    GMAIL_APP_PASSWORD=앱비밀번호16자리

  Gmail 앱 비밀번호 발급 방법:
    1. Google 계정 → 보안 → 2단계 인증 확인(켜져 있어야 함)
    2. Google 계정 → 보안 → 앱 비밀번호 → '앱 선택: 기타' → 이름 입력 → 생성
    3. 발급된 16자리 비밀번호를 GMAIL_APP_PASSWORD에 입력

  .env 예시 (프로젝트 루트에 .env 파일 생성):
    GMAIL_USER=mysender@gmail.com
    GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
"""

from __future__ import annotations

import os
import re
import sys
import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"


def load_env() -> None:
    """프로젝트 루트의 .env 파일에서 환경변수 로드 (python-dotenv 없이)."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def build_email_body(client_id: str, report_date: str,
                     kyc: dict, risk: dict) -> tuple[str, str]:
    """안내문만 담은 이메일 본문 반환. 리포트는 첨부파일로 전달."""
    profile   = kyc.get("profile", {})
    age_group = profile.get("age_group", "고객")
    score     = risk.get("total_score", "?")
    grade     = risk.get("grade", "")

    subject = f"[자산관리 AI 진단] {age_group} 고객님의 재무 건강 리포트 ({report_date})"

    body = f"""
<div style="font-family: 'Noto Sans KR', Arial, sans-serif; max-width:600px; margin:auto; padding:32px 24px; color:#222;">
  <h2 style="color:#2c3e50; margin-bottom:8px;">AI 재무 건강 리포트가 도착했습니다</h2>
  <p style="margin-top:0; color:#555;">안녕하세요, {age_group} 고객님.</p>

  <table style="border-collapse:collapse; background:#f8f9fa; border-radius:8px; padding:16px; margin:20px 0; width:100%;">
    <tr>
      <td style="padding:8px 16px; font-weight:bold; color:#555; width:120px;">종합 점수</td>
      <td style="padding:8px 16px; font-size:18px; font-weight:bold;">{score}점 {grade}</td>
    </tr>
    <tr>
      <td style="padding:8px 16px; font-weight:bold; color:#555;">진단 일자</td>
      <td style="padding:8px 16px;">{report_date}</td>
    </tr>
  </table>

  <p style="color:#333;">첨부된 <strong>재무진단리포트_{report_date}.html</strong> 파일을 브라우저로 열어 전체 내용을 확인하세요.</p>

  <hr style="border:none; border-top:1px solid #eee; margin:28px 0;">
  <p style="font-size:12px; color:#999; line-height:1.6;">
    본 리포트는 AI가 생성한 참고용 자료이며, 실제 투자 손익에 대한 책임은 전적으로 투자자 본인에게 있습니다.<br>
    세금·법률 사항은 반드시 전문가에게 확인하세요.
  </p>
</div>
"""
    return subject, body


def send(client_id: str, to_email: str, report_date: str) -> bool:
    """
    리포트 발송 메인 함수.
    Returns True on success, False on failure.
    """
    load_env()

    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")

    if not _EMAIL_RE.match(to_email):
        print(f"[ERROR] 잘못된 이메일 형식: '{to_email}'")
        return False

    if not gmail_user or not gmail_pass:
        print("[ERROR] Gmail 발송 설정이 없습니다.")
        print("  → 프로젝트 루트에 .env 파일을 생성하고 아래 내용을 입력하세요:")
        print("      GMAIL_USER=보내는Gmail주소@gmail.com")
        print("      GMAIL_APP_PASSWORD=앱비밀번호16자리")
        print("  → Gmail 앱 비밀번호: Google 계정 → 보안 → 앱 비밀번호에서 발급")
        return False

    client_dir  = DATA_DIR / "clients" / client_id
    reports_dir = client_dir / "reports"
    html_path   = reports_dir / f"{report_date}.html"
    kyc_path    = client_dir / "kyc.json"
    risk_path   = client_dir / "risk_score.json"

    if not html_path.exists():
        print(f"[ERROR] 리포트 없음: {html_path}")
        return False

    # KYC / 점수 로드 (제목·인트로용)
    kyc  = json.loads(kyc_path.read_text(encoding="utf-8"))  if kyc_path.exists()  else {}
    risk = json.loads(risk_path.read_text(encoding="utf-8")) if risk_path.exists() else {}

    subject, body_html = build_email_body(client_id, report_date, kyc, risk)

    # 이메일 구성
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = to_email

    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # HTML 파일을 첨부파일로도 추가 (오프라인 보관용)
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(html_path.read_bytes())
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"재무진단리포트_{report_date}.html",
    )
    msg.attach(attachment)

    # Gmail SMTP 발송
    try:
        print(f"  [발송 중] {gmail_user} → {to_email} ...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_bytes())
        print(f"  [발송 완료] 제목: {subject}")
        # 발송 로그 기록
        LOGS_DIR.mkdir(exist_ok=True)
        log_line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {client_id} → {to_email} | {subject}\n"
        with open(LOGS_DIR / "email_sent.log", "a", encoding="utf-8") as f:
            f.write(log_line)
        return True
    except smtplib.SMTPAuthenticationError:
        print("[ERROR] Gmail 인증 실패 — 앱 비밀번호를 확인하세요.")
        print("  (일반 Gmail 비밀번호가 아닌 앱 비밀번호를 사용해야 합니다)")
        return False
    except Exception as e:
        print(f"[ERROR] 발송 실패: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("사용법: python scripts/send_report.py <client_id> <to_email> <report_date(YYYY-MM-DD)>")
        sys.exit(1)
    ok = send(sys.argv[1], sys.argv[2], sys.argv[3])
    sys.exit(0 if ok else 1)
