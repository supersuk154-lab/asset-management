"""
utils.py — 파이프라인 공통 유틸리티

safe_write_json: 원자적 JSON 파일 쓰기.
임시 파일에 먼저 쓴 뒤 os.replace로 교체하여, 키보드 인터럽트·타임아웃 등으로
스크립트가 중단되더라도 기존 파일이 절반만 쓰인 상태로 손상되지 않는다.

safe_read_json: 안전한 JSON 파일 읽기.
파일 없음·파싱 오류 시 기본값을 반환하며 예외를 전파하지 않는다.
"""

import json
import os
from pathlib import Path


def safe_read_json(file_path: Path | str, default_val: dict | None = None) -> dict:
    """JSON 파일을 안전하게 읽고, 파일 없음·파싱 오류 시 기본값을 반환한다.

    status 체크·캐시 날짜 확인 등 실패해도 무관한 읽기에 사용한다.
    파일 손상 경고가 필요한 경우(예: finalize history.json)는 직접 try-except를 쓸 것.
    """
    path = Path(file_path)
    if not path.exists():
        return default_val if default_val is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_val if default_val is not None else {}


def safe_write_json(file_path: Path | str, data) -> None:
    """임시 파일에 먼저 쓴 뒤 os.replace 로 원본을 원자적으로 교체한다.
    쓰기 실패 시 .tmp 잔류 파일을 자동 정리하고 예외를 재전파한다.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, file_path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
