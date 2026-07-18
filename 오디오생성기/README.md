# 오디오생성기

텍스트 대본을 두 명 이상의 화자가 대화하는 MP3 음성 파일로 자동 변환하는 프로그램.
상세 설계는 [기획안.md](기획안.md) 참조.

## 빠른 시작

```bash
# 1. 의존성 설치 (최초 1회)
pip install -r requirements.txt
# + ffmpeg 필요: sudo apt-get install ffmpeg  (Windows는 ffmpeg.org에서 다운로드)

# 2. 대본 작성 → input/ 폴더에 저장
#    형식:  화자이름: [감정] 대사

# 3. 실행
python scripts/pipeline.py input/샘플.txt

# 결과: output/샘플.mp3
```

## 대본 형식 예시

```
A: [반갑게] 야, 진짜 오랜만이다!
B: 그러게... 몇 년 만이지?
```

- `이름:` 으로 시작하는 줄 = 새 대사. 그 외 줄은 직전 대사에 이어 붙음
- `[감정]` 또는 `(감정)` 태그는 선택 사항
- 화자별 목소리는 `voices.json`에서 지정 (미지정 시 등장 순서대로 자동 배정)

## TTS 엔진

| 엔진 | 명령 | 특징 |
|---|---|---|
| edge (기본) | `python scripts/pipeline.py input/대본.txt` | 무료 Microsoft 음성, 자연스러운 한국어, **인터넷 필요** |
| espeak | `python scripts/pipeline.py input/대본.txt --engine espeak` | 완전 오프라인, 기계음 (테스트/백업용) |

> 참고: Claude Code 원격(웹) 세션에서는 네트워크 정책상 edge-tts 서버 접속이 차단되어
> espeak 엔진만 동작한다. 본인 PC에서 실행하면 edge 엔진이 정상 동작한다.

## 폴더 구조

```
scripts/pipeline.py     ← 전체 실행 (파싱 → 합성 → 병합)
scripts/parse_script.py ← 대본 → work/dialogue.json
scripts/synthesize.py   ← 대사별 WAV 합성 (edge-tts / espeak)
scripts/merge_audio.py  ← WAV 병합 + 무음 삽입 + 최종 MP3 인코딩
voices.json             ← 화자 → 목소리 매핑
input/                  ← 대본 넣는 곳
output/                 ← 완성된 MP3 (git 제외)
work/                   ← 중간 산출물 (git 제외)
```
