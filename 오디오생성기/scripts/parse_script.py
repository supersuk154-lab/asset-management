"""Step 1: 대본 텍스트 → work/dialogue.json 구조화.

대본 형식:
    화자이름: [감정] 대사 내용
    화자이름: 대사 내용
줄 앞에 "이름:"이 없는 줄은 직전 대사의 연속으로 붙인다.
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# 화자 라벨: 콜론 앞 1~20자 (콜론·줄바꿈 제외). "6:30" 같은 시각 표기는 화자로 오인하지 않도록 숫자-only 라벨 제외
SPEAKER_RE = re.compile(r"^\s*(?P<speaker>(?!\d+\s*:)[^:\n]{1,20}?)\s*:\s*(?P<text>.+)$")
EMOTION_RE = re.compile(r"^[\[\(]\s*([^\]\)]+?)\s*[\]\)]\s*")


def parse_text(text: str) -> list[dict]:
    lines: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = SPEAKER_RE.match(raw)
        if m:
            if current:
                lines.append(current)
            body = m.group("text").strip()
            emotion = None
            em = EMOTION_RE.match(body)
            if em:
                emotion = em.group(1)
                body = body[em.end():].strip()
            current = {
                "seq": len(lines) + 1,
                "speaker": m.group("speaker").strip(),
                "emotion": emotion,
                "text": body,
            }
        elif current:
            current["text"] += " " + raw.strip()
        # 첫 화자 등장 전의 지문/제목 줄은 무시
    if current:
        lines.append(current)
    return lines


def assign_voices(lines: list[dict], voices_cfg: dict) -> None:
    """등장 순서대로 화자에게 목소리를 배정한다. voices.json의 speakers 지정이 우선."""
    fixed = voices_cfg.get("speakers", {})
    rotation = voices_cfg.get("default_rotation", [])
    espeak_rotation = voices_cfg.get("espeak_rotation", [])
    pause = voices_cfg.get("default_pause_after_ms", 700)

    order: list[str] = []
    for ln in lines:
        if ln["speaker"] not in order:
            order.append(ln["speaker"])

    for ln in lines:
        idx = order.index(ln["speaker"])
        ln["voice"] = fixed.get(ln["speaker"]) or rotation[idx % len(rotation)]
        ln["espeak"] = espeak_rotation[idx % len(espeak_rotation)] if espeak_rotation else {"voice": "ko"}
        ln.setdefault("tts_params", {"rate": "+0%", "pitch": "+0Hz"})
        ln.setdefault("pause_after_ms", pause)


def main(input_path: str) -> Path:
    src = Path(input_path)
    text = src.read_text(encoding="utf-8")
    lines = parse_text(text)
    if not lines:
        sys.exit(f"[parse] FAIL: '{src}'에서 '화자: 대사' 형식을 찾지 못했습니다.")

    voices_cfg = json.loads((BASE / "voices.json").read_text(encoding="utf-8"))
    assign_voices(lines, voices_cfg)

    out = {"title": src.stem, "source": str(src), "lines": lines}
    work = BASE / "work"
    work.mkdir(exist_ok=True)
    out_path = work / "dialogue.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    speakers = sorted({ln["speaker"] for ln in lines})
    print(f"[parse] PASS: 대사 {len(lines)}건, 화자 {len(speakers)}명 {speakers} → {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python parse_script.py <대본.txt>")
    main(sys.argv[1])
