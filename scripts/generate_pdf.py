"""
HTML 리포트 → PDF 변환 스크립트

방법 1 (권장): weasyprint
  pip install weasyprint

방법 2 (대안): pdfkit + wkhtmltopdf
  pip install pdfkit
  wkhtmltopdf 설치 필요: https://wkhtmltopdf.org/downloads.html

사용법:
  python scripts/generate_pdf.py client_20260527_001 2026-05-27
  python scripts/generate_pdf.py --all  # 모든 미변환 리포트 일괄 변환
"""

import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "clients"


def html_to_pdf_weasyprint(html_path: Path, pdf_path: Path) -> bool:
    try:
        from weasyprint import HTML
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True
    except ImportError:
        print("[오류] weasyprint 없음: pip install weasyprint")
        return False
    except Exception as e:
        print(f"[오류] weasyprint 변환 실패: {e}")
        return False


def html_to_pdf_pdfkit(html_path: Path, pdf_path: Path) -> bool:
    try:
        import pdfkit
        options = {
            "encoding": "UTF-8",
            "page-size": "A4",
            "margin-top": "10mm",
            "margin-bottom": "10mm",
            "margin-left": "10mm",
            "margin-right": "10mm",
            "enable-local-file-access": None,
        }
        pdfkit.from_file(str(html_path), str(pdf_path), options=options)
        return True
    except ImportError:
        print("[오류] pdfkit 없음: pip install pdfkit")
        return False
    except Exception as e:
        print(f"[오류] pdfkit 변환 실패: {e}")
        return False


def convert_report(client_id: str, date: str) -> bool:
    """특정 고객의 특정 날짜 리포트를 PDF로 변환"""
    client_dir = DATA_DIR / client_id / "reports"
    html_path = client_dir / f"{date}.html"
    pdf_path = client_dir / f"{date}.pdf"

    if not html_path.exists():
        print(f"[오류] HTML 없음: {html_path}")
        return False

    if pdf_path.exists():
        print(f"[스킵] 이미 존재: {pdf_path.name}")
        return True

    print(f"변환 중: {html_path.name} → {pdf_path.name}")

    # weasyprint 먼저 시도, 실패 시 pdfkit
    if html_to_pdf_weasyprint(html_path, pdf_path):
        print(f"[완료] {pdf_path}")
        return True
    elif html_to_pdf_pdfkit(html_path, pdf_path):
        print(f"[완료] {pdf_path}")
        return True
    else:
        print("[실패] PDF 변환 불가. weasyprint 또는 pdfkit+wkhtmltopdf 설치 필요.")
        return False


def convert_all():
    """data/clients/ 아래 모든 HTML 리포트를 PDF로 변환"""
    html_files = list(DATA_DIR.glob("*/reports/*.html"))
    if not html_files:
        print("변환할 HTML 리포트가 없습니다.")
        return

    success = 0
    for html_path in html_files:
        date = html_path.stem
        client_id = html_path.parent.parent.name
        if convert_report(client_id, date):
            success += 1

    print(f"\n완료: {success}/{len(html_files)}개 변환")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--all":
        convert_all()
    elif len(sys.argv) == 3:
        convert_report(sys.argv[1], sys.argv[2])
    else:
        print("사용법:")
        print("  python generate_pdf.py client_20260527_001 2026-05-27")
        print("  python generate_pdf.py --all")
