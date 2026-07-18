"""Step 4: WAV 세그먼트 병합 + 최종 MP3 인코딩.

원칙 (기획안 5.2): 모든 중간 처리는 무손실 WAV로 수행하고,
MP3 인코딩은 최종 export 단 한 번만 적용한다 (접합부 팝핑 노이즈 방지).
"""
import json
import sys
from pathlib import Path

from pydub import AudioSegment

BASE = Path(__file__).resolve().parent.parent
FRAME_RATE = 24000
LEAD_IN_MS = 300   # 시작 여백
LEAD_OUT_MS = 500  # 끝 여백
BITRATE = "192k"


def main() -> Path:
    dialogue = json.loads((BASE / "work" / "dialogue.json").read_text(encoding="utf-8"))
    seg_dir = BASE / "work" / "segments"

    combined = AudioSegment.silent(duration=LEAD_IN_MS, frame_rate=FRAME_RATE)
    total_lines = len(dialogue["lines"])
    for i, line in enumerate(dialogue["lines"]):
        wav = seg_dir / f"{line['seq']:03d}.wav"
        if not wav.exists():
            sys.exit(f"[merge] FAIL: 세그먼트 누락 {wav}")
        combined += AudioSegment.from_wav(wav)
        if i < total_lines - 1:  # 마지막 대사 뒤에는 pause 대신 LEAD_OUT
            combined += AudioSegment.silent(
                duration=line.get("pause_after_ms", 700), frame_rate=FRAME_RATE
            )
    combined += AudioSegment.silent(duration=LEAD_OUT_MS, frame_rate=FRAME_RATE)

    out_dir = BASE / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{dialogue['title']}.mp3"
    combined.export(out_path, format="mp3", bitrate=BITRATE)

    print(f"[merge] PASS: {out_path} (길이 {len(combined)/1000:.1f}초, {BITRATE})")
    return out_path


if __name__ == "__main__":
    main()
