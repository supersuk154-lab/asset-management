"""Step 3: dialogue.json → 대사별 WAV 세그먼트 생성 (work/segments/NNN.wav).

엔진:
  edge   — Microsoft edge-tts (무료, 인터넷 필요, 자연스러운 한국어) [기본]
  espeak — espeak-ng (완전 오프라인, 기계음이지만 네트워크 차단 환경에서 테스트용)

모든 세그먼트는 무손실 WAV(mono 24kHz)로 저장하며, 앞뒤 무음을 잘라내
병합 시 호흡(무음)을 순수하게 pause_after_ms로만 제어한다.
"""
import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_leading_silence

BASE = Path(__file__).resolve().parent.parent
SEG_DIR = BASE / "work" / "segments"
FRAME_RATE = 24000
CONCURRENCY = 4  # edge-tts 동시 호출 제한 (서버 부하 배려)


def trim_silence(seg: AudioSegment, threshold_db: float = -45.0, keep_ms: int = 50) -> AudioSegment:
    start = detect_leading_silence(seg, silence_threshold=threshold_db)
    end = detect_leading_silence(seg.reverse(), silence_threshold=threshold_db)
    start = max(0, start - keep_ms)
    end = max(0, end - keep_ms)
    return seg[start: len(seg) - end]


def normalize(seg: AudioSegment) -> AudioSegment:
    return seg.set_frame_rate(FRAME_RATE).set_channels(1).set_sample_width(2)


async def synth_edge_one(line: dict, sem: asyncio.Semaphore) -> None:
    import edge_tts

    params = line.get("tts_params", {})
    async with sem:
        comm = edge_tts.Communicate(
            line["text"],
            line["voice"],
            rate=params.get("rate", "+0%"),
            pitch=params.get("pitch", "+0Hz"),
        )
        buf = io.BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
    buf.seek(0)
    seg = normalize(trim_silence(AudioSegment.from_file(buf, format="mp3")))
    seg.export(SEG_DIR / f"{line['seq']:03d}.wav", format="wav")
    print(f"[synth] {line['seq']:03d} {line['speaker']} ({line['voice']}) {len(seg)/1000:.1f}s")


def synth_espeak_one(line: dict) -> None:
    cfg = line.get("espeak", {"voice": "ko"})
    out = SEG_DIR / f"{line['seq']:03d}.wav"
    subprocess.run(
        [
            "espeak-ng",
            "-v", cfg.get("voice", "ko"),
            "-p", str(cfg.get("pitch", 50)),
            "-s", str(cfg.get("speed", 160)),
            "-w", str(out),
            line["text"],
        ],
        check=True,
    )
    seg = normalize(trim_silence(AudioSegment.from_wav(out)))
    seg.export(out, format="wav")
    print(f"[synth] {line['seq']:03d} {line['speaker']} (espeak p{cfg.get('pitch')}) {len(seg)/1000:.1f}s")


async def run_edge(lines: list[dict]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(synth_edge_one(ln, sem) for ln in lines))


def main(engine: str = "edge") -> None:
    dialogue = json.loads((BASE / "work" / "dialogue.json").read_text(encoding="utf-8"))
    lines = dialogue["lines"]
    SEG_DIR.mkdir(parents=True, exist_ok=True)
    for old in SEG_DIR.glob("*.wav"):
        old.unlink()

    if engine == "edge":
        asyncio.run(run_edge(lines))
    elif engine == "espeak":
        for ln in lines:
            synth_espeak_one(ln)
    else:
        sys.exit(f"[synth] FAIL: 알 수 없는 엔진 '{engine}' (edge | espeak)")

    made = len(list(SEG_DIR.glob("*.wav")))
    if made != len(lines):
        sys.exit(f"[synth] FAIL: 대사 {len(lines)}건 중 {made}건만 생성됨")
    print(f"[synth] PASS: 세그먼트 {made}건 생성 완료 ({engine})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "edge")
