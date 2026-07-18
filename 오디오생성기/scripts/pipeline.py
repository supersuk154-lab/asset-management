"""오디오생성기 파이프라인 일괄 실행 CLI.

사용법:
    python scripts/pipeline.py input/샘플.txt                # edge-tts (기본)
    python scripts/pipeline.py input/샘플.txt --engine espeak # 오프라인 엔진
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_audio
import parse_script
import synthesize


def check_prereqs(engine: str) -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "[사전검사] FAIL: ffmpeg이 설치되어 있지 않습니다.\n"
            "  Ubuntu/Debian: sudo apt-get install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html 후 PATH 등록\n"
            "  macOS: brew install ffmpeg"
        )
    if engine == "espeak" and shutil.which("espeak-ng") is None:
        sys.exit("[사전검사] FAIL: espeak-ng 미설치 (sudo apt-get install espeak-ng)")
    print(f"[사전검사] PASS: ffmpeg 확인 완료 (엔진: {engine})")


def main() -> None:
    ap = argparse.ArgumentParser(description="대본 → 다화자 대화 MP3 자동 생성")
    ap.add_argument("input", help="대본 텍스트 파일 경로 (예: input/샘플.txt)")
    ap.add_argument("--engine", default="edge", choices=["edge", "espeak"],
                    help="TTS 엔진 (edge=고품질/인터넷 필요, espeak=오프라인)")
    args = ap.parse_args()

    if not Path(args.input).exists():
        sys.exit(f"[사전검사] FAIL: 대본 파일 없음 — {args.input}")

    check_prereqs(args.engine)
    parse_script.main(args.input)      # Step 1: 대본 → dialogue.json
    synthesize.main(args.engine)       # Step 3: 대사별 WAV 합성
    out = merge_audio.main()           # Step 4: 병합 + MP3 인코딩
    print(f"\n✅ 완료: {out}")


if __name__ == "__main__":
    main()
