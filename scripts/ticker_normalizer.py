"""
ticker_normalizer.py — 종목명 정규화 (Ticker Normalization)

역할:
  kyc-collector가 파싱한 kyc.json의 investments 배열을 읽어,
  별명·오타·비공식 약칭이 섞인 문자열을 표준 종목명·티커·섹터로 확정한다.
  LLM에게 "이 종목이 뭔지 추측해라"고 요구하는 대신,
  파이썬 정적 사전 + 퍼지 매칭으로 정확성을 보장한다.

매칭 순서:
  1. 정규화 후 정확 매칭 (정적 사전 aliases)
  2. 퍼지 매칭 (TheFuzz — 없으면 정확 매칭만 수행)
     - confidence >= FUZZY_THRESHOLD_HIGH(85) → 자동 확정
     - confidence  65~84 → 저신뢰 경고, 진행하되 needs_review: true
     - confidence <  65  → unresolved, needs_review: true
  3. 매칭 실패 → raw_name 유지, match_type: "unresolved"

출력:
  kyc.json investments 배열의 각 항목이 아래 구조로 확장됨:
  {
    "raw_name":      "삼전",          ← 원본 입력 보존
    "standard_name": "삼성전자",      ← 확정된 공식 종목명
    "ticker":        "005930.KS",    ← Yahoo Finance 티커
    "sector":        "IT/반도체",    ← 섹터 분류
    "market":        "KR",           ← KR / KR_ETF / US / US_ETF
    "amount":        5000000,
    "match_type":    "alias_exact",  ← alias_exact / fuzzy / unresolved
    "match_confidence": 100
  }

  unresolved 종목이 1개라도 있으면 kyc.status.needs_review = true

필요 패키지 (선택):
  pip install thefuzz python-Levenshtein
  ※ 미설치 시 퍼지 매칭 생략, 정확 매칭만 수행 (기능 저하)

사용법:
  python scripts/ticker_normalizer.py <client_id>
  예) python scripts/ticker_normalizer.py client_20260527_001
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from utils import safe_write_json

# Windows CP949 콘솔에서 이모지·특수문자 출력 시 인코딩 크래시 방지
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR = Path(__file__).parent.parent

FUZZY_THRESHOLD_HIGH = 85   # 이상: 자동 확정
FUZZY_THRESHOLD_LOW  = 65   # 이상 85 미만: 저신뢰 경고 후 사용, needs_review


# ═══════════════════════════════════════════════════════════
# 정적 종목 사전 (Top 200 + 주요 ETF)
# ───────────────────────────────────────────────────────────
# 구조: { "TICKER_KEY": { standard_name, ticker, sector, market, aliases[] } }
# aliases: 소문자·띄어쓰기 제거 후 매칭. 별명·약칭·오타·영문명 포함.
# 새 종목 추가 시 aliases에 행을 추가하면 즉시 반영됨.
# ═══════════════════════════════════════════════════════════

TICKER_DICT: dict[str, dict] = {

    # ── 국내 대형주 ────────────────────────────────────────────
    "005930.KS": {
        "standard_name": "삼성전자",
        "ticker": "005930.KS", "sector": "IT/반도체", "market": "KR",
        "aliases": ["삼성전자", "삼전", "삼성", "samsung", "samsungelectronics", "005930"],
    },
    "000660.KS": {
        "standard_name": "SK하이닉스",
        "ticker": "000660.KS", "sector": "IT/반도체", "market": "KR",
        "aliases": ["sk하이닉스", "하이닉스", "하닉", "skhynix", "sk하닉", "000660"],
    },
    "005380.KS": {
        "standard_name": "현대차",
        "ticker": "005380.KS", "sector": "자동차", "market": "KR",
        "aliases": ["현대차", "현대자동차", "hyundai", "현대", "005380"],
    },
    "000270.KS": {
        "standard_name": "기아",
        "ticker": "000270.KS", "sector": "자동차", "market": "KR",
        "aliases": ["기아", "기아차", "기아자동차", "kia", "000270"],
    },
    "373220.KS": {
        "standard_name": "LG에너지솔루션",
        "ticker": "373220.KS", "sector": "배터리/2차전지", "market": "KR",
        "aliases": ["lg에너지솔루션", "엘지에너지", "lges", "lg에너지", "373220"],
    },
    "207940.KS": {
        "standard_name": "삼성바이오로직스",
        "ticker": "207940.KS", "sector": "바이오/제약", "market": "KR",
        "aliases": ["삼성바이오로직스", "삼바", "삼성바이오", "207940"],
    },
    "068270.KS": {
        "standard_name": "셀트리온",
        "ticker": "068270.KS", "sector": "바이오/제약", "market": "KR",
        "aliases": ["셀트리온", "셀트", "celltrion", "068270"],
    },
    "005490.KS": {
        "standard_name": "POSCO홀딩스",
        "ticker": "005490.KS", "sector": "철강/소재", "market": "KR",
        "aliases": ["posco홀딩스", "포스코홀딩스", "포스코", "posco", "005490"],
    },
    "035720.KS": {
        "standard_name": "카카오",
        "ticker": "035720.KS", "sector": "IT/플랫폼", "market": "KR",
        "aliases": ["카카오", "kakao", "035720"],
    },
    "035420.KS": {
        "standard_name": "NAVER",
        "ticker": "035420.KS", "sector": "IT/플랫폼", "market": "KR",
        "aliases": ["naver", "네이버", "035420"],
    },
    "051910.KS": {
        "standard_name": "LG화학",
        "ticker": "051910.KS", "sector": "화학/배터리", "market": "KR",
        "aliases": ["lg화학", "엘지화학", "051910"],
    },
    "006400.KS": {
        "standard_name": "삼성SDI",
        "ticker": "006400.KS", "sector": "배터리/2차전지", "market": "KR",
        "aliases": ["삼성sdi", "삼성에스디아이", "sdi", "006400"],
    },
    "012330.KS": {
        "standard_name": "현대모비스",
        "ticker": "012330.KS", "sector": "자동차부품", "market": "KR",
        "aliases": ["현대모비스", "모비스", "012330"],
    },
    "105560.KS": {
        "standard_name": "KB금융",
        "ticker": "105560.KS", "sector": "금융/은행", "market": "KR",
        "aliases": ["kb금융", "kb", "국민은행", "105560"],
    },
    "055550.KS": {
        "standard_name": "신한지주",
        "ticker": "055550.KS", "sector": "금융/은행", "market": "KR",
        "aliases": ["신한지주", "신한", "신한은행", "055550"],
    },
    "086790.KS": {
        "standard_name": "하나금융지주",
        "ticker": "086790.KS", "sector": "금융/은행", "market": "KR",
        "aliases": ["하나금융지주", "하나금융", "하나은행", "086790"],
    },
    "316140.KS": {
        "standard_name": "우리금융지주",
        "ticker": "316140.KS", "sector": "금융/은행", "market": "KR",
        "aliases": ["우리금융지주", "우리금융", "우리은행", "316140"],
    },
    "032830.KS": {
        "standard_name": "삼성생명",
        "ticker": "032830.KS", "sector": "금융/보험", "market": "KR",
        "aliases": ["삼성생명", "032830"],
    },
    "000810.KS": {
        "standard_name": "삼성화재",
        "ticker": "000810.KS", "sector": "금융/보험", "market": "KR",
        "aliases": ["삼성화재", "000810"],
    },
    "259960.KS": {
        "standard_name": "크래프톤",
        "ticker": "259960.KS", "sector": "IT/게임", "market": "KR",
        "aliases": ["크래프톤", "krafton", "배그회사", "259960"],
    },
    "036570.KS": {
        "standard_name": "엔씨소프트",
        "ticker": "036570.KS", "sector": "IT/게임", "market": "KR",
        "aliases": ["엔씨소프트", "엔씨", "nc", "ncsoft", "036570"],
    },
    "251270.KS": {
        "standard_name": "넷마블",
        "ticker": "251270.KS", "sector": "IT/게임", "market": "KR",
        "aliases": ["넷마블", "251270"],
    },
    "015760.KS": {
        "standard_name": "한국전력",
        "ticker": "015760.KS", "sector": "유틸리티", "market": "KR",
        "aliases": ["한국전력", "한전", "kepco", "015760"],
    },
    "034020.KS": {
        "standard_name": "두산에너빌리티",
        "ticker": "034020.KS", "sector": "에너지/중공업", "market": "KR",
        "aliases": ["두산에너빌리티", "두산중공업", "두에", "034020"],
    },
    "329180.KS": {
        "standard_name": "HD현대중공업",
        "ticker": "329180.KS", "sector": "조선", "market": "KR",
        "aliases": ["hd현대중공업", "현대중공업", "hd중공업", "329180"],
    },
    "009540.KS": {
        "standard_name": "HD한국조선해양",
        "ticker": "009540.KS", "sector": "조선", "market": "KR",
        "aliases": ["hd한국조선해양", "한국조선해양", "009540"],
    },
    "042660.KS": {
        "standard_name": "한화오션",
        "ticker": "042660.KS", "sector": "조선", "market": "KR",
        "aliases": ["한화오션", "대우조선해양", "042660"],
    },
    "012450.KS": {
        "standard_name": "한화에어로스페이스",
        "ticker": "012450.KS", "sector": "방산/항공", "market": "KR",
        "aliases": ["한화에어로스페이스", "한화에어로", "한화방산", "012450"],
    },
    "047810.KS": {
        "standard_name": "한국항공우주",
        "ticker": "047810.KS", "sector": "방산/항공", "market": "KR",
        "aliases": ["한국항공우주", "kai", "047810"],
    },
    "066570.KS": {
        "standard_name": "LG전자",
        "ticker": "066570.KS", "sector": "전자/가전", "market": "KR",
        "aliases": ["lg전자", "엘지전자", "066570"],
    },
    "017670.KS": {
        "standard_name": "SK텔레콤",
        "ticker": "017670.KS", "sector": "통신", "market": "KR",
        "aliases": ["sk텔레콤", "skt", "017670"],
    },
    "030200.KS": {
        "standard_name": "KT",
        "ticker": "030200.KS", "sector": "통신", "market": "KR",
        "aliases": ["kt", "한국통신", "030200"],
    },
    "032640.KS": {
        "standard_name": "LG유플러스",
        "ticker": "032640.KS", "sector": "통신", "market": "KR",
        "aliases": ["lg유플러스", "유플러스", "lgu+", "032640"],
    },
    "096770.KS": {
        "standard_name": "SK이노베이션",
        "ticker": "096770.KS", "sector": "에너지/배터리", "market": "KR",
        "aliases": ["sk이노베이션", "sk이노", "096770"],
    },
    "010950.KS": {
        "standard_name": "S-Oil",
        "ticker": "010950.KS", "sector": "에너지/정유", "market": "KR",
        "aliases": ["s-oil", "에쓰오일", "s오일", "010950"],
    },
    "011200.KS": {
        "standard_name": "HMM",
        "ticker": "011200.KS", "sector": "해운", "market": "KR",
        "aliases": ["hmm", "현대상선", "011200"],
    },
    "010130.KS": {
        "standard_name": "고려아연",
        "ticker": "010130.KS", "sector": "철강/소재", "market": "KR",
        "aliases": ["고려아연", "010130"],
    },
    "018260.KS": {
        "standard_name": "삼성에스디에스",
        "ticker": "018260.KS", "sector": "IT/서비스", "market": "KR",
        "aliases": ["삼성에스디에스", "삼성sds", "sds", "018260"],
    },
    "128940.KS": {
        "standard_name": "한미약품",
        "ticker": "128940.KS", "sector": "바이오/제약", "market": "KR",
        "aliases": ["한미약품", "128940"],
    },
    "068760.KS": {
        "standard_name": "셀트리온헬스케어",
        "ticker": "068760.KS", "sector": "바이오/제약", "market": "KR",
        "aliases": ["셀트리온헬스케어", "셀헬", "068760"],
    },
    "028260.KS": {
        "standard_name": "삼성물산",
        "ticker": "028260.KS", "sector": "건설/유통", "market": "KR",
        "aliases": ["삼성물산", "028260"],
    },
    "000720.KS": {
        "standard_name": "현대건설",
        "ticker": "000720.KS", "sector": "건설", "market": "KR",
        "aliases": ["현대건설", "000720"],
    },
    "024110.KS": {
        "standard_name": "기업은행",
        "ticker": "024110.KS", "sector": "금융/은행", "market": "KR",
        "aliases": ["기업은행", "ibk", "024110"],
    },

    # ── 국내 ETF (신규 / 알파뉴메릭 코드) ──────────────────────
    "0177N0.KS": {
        "standard_name": "KODEX 삼성전자SK하이닉스채권혼합50",
        "ticker": "0177N0.KS", "sector": "혼합/채권혼합ETF", "market": "KR_ETF",
        "aliases": [
            "kodex삼성전자sk하이닉스채권혼합50", "kodex삼전하이닉스채권혼합50",
            "삼성전자sk하이닉스채권혼합50", "kodex삼성전자하이닉스채권혼합", "0177n0",
        ],
    },
    "0115E0.KS": {
        "standard_name": "KODEX 코리아소버린AI",
        "ticker": "0115E0.KS", "sector": "국내/AI테마ETF", "market": "KR_ETF",
        "aliases": [
            "kodex코리아소버린ai", "kodex코리아소버린에이아이", "코리아소버린ai",
            "kodex코리아소버린", "0115e0",
        ],
    },
    "0174B0.KS": {
        "standard_name": "KoAct 글로벌AI메모리반도체액티브",
        "ticker": "0174B0.KS", "sector": "글로벌/AI반도체ETF", "market": "KR_ETF",
        "aliases": [
            "koact글로벌ai메모리반도체액티브", "koact글로벌ai메모리반도체",
            "글로벌ai메모리반도체액티브", "koact글로벌에이아이메모리반도체", "0174b0",
        ],
    },

    # ── 국내 ETF ───────────────────────────────────────────────
    "069500.KS": {
        "standard_name": "KODEX 200",
        "ticker": "069500.KS", "sector": "국내/대형주ETF", "market": "KR_ETF",
        "aliases": ["kodex200", "kodex200etf", "코덱스200", "코스피etf", "069500"],
    },
    "102110.KS": {
        "standard_name": "TIGER 200",
        "ticker": "102110.KS", "sector": "국내/대형주ETF", "market": "KR_ETF",
        "aliases": ["tiger200", "타이거200", "102110"],
    },
    "360750.KS": {
        "standard_name": "TIGER 미국S&P500TR",
        "ticker": "360750.KS", "sector": "미국/S&P500ETF", "market": "KR_ETF",
        "aliases": ["tiger미국s&p500tr", "tigers&p500tr", "타이거sp500tr", "s&p500tr", "360750"],
    },
    "379800.KS": {
        "standard_name": "KODEX 미국S&P500TR",
        "ticker": "379800.KS", "sector": "미국/S&P500ETF", "market": "KR_ETF",
        "aliases": ["kodex미국s&p500tr", "코덱스sp500tr", "379800"],
    },
    "133690.KS": {
        "standard_name": "TIGER 미국나스닥100",
        "ticker": "133690.KS", "sector": "미국/나스닥ETF", "market": "KR_ETF",
        "aliases": ["tiger미국나스닥100", "tiger나스닥100", "타이거나스닥", "133690"],
    },
    "304940.KS": {
        "standard_name": "KODEX 나스닥100TR",
        "ticker": "304940.KS", "sector": "미국/나스닥ETF", "market": "KR_ETF",
        "aliases": ["kodex나스닥100tr", "코덱스나스닥tr", "304940"],
    },
    "367380.KS": {
        "standard_name": "TIGER 미국S&P500",
        "ticker": "367380.KS", "sector": "미국/S&P500ETF", "market": "KR_ETF",
        "aliases": ["tiger미국s&p500", "tigers&p500", "타이거sp500", "367380"],
    },
    "195930.KS": {
        "standard_name": "TIGER 해외선진국MSCI World",
        "ticker": "195930.KS", "sector": "선진국/글로벌ETF", "market": "KR_ETF",
        "aliases": ["tigermsciworld", "tiger해외선진국", "msciworldetf", "195930"],
    },
    "091160.KS": {
        "standard_name": "KODEX 반도체",
        "ticker": "091160.KS", "sector": "국내/섹터ETF", "market": "KR_ETF",
        "aliases": ["kodex반도체", "코덱스반도체", "091160"],
    },
    "117460.KS": {
        "standard_name": "KODEX 배당가치",
        "ticker": "117460.KS", "sector": "국내/배당ETF", "market": "KR_ETF",
        "aliases": ["kodex배당가치", "코덱스배당", "117460"],
    },
    "329200.KS": {
        "standard_name": "TIGER 차이나항셍테크",
        "ticker": "329200.KS", "sector": "중국/기술주ETF", "market": "KR_ETF",
        "aliases": ["tiger차이나항셍테크", "tiger항셍테크", "항셍테크etf", "329200"],
    },
    "148070.KS": {
        "standard_name": "TIGER 미국채10년선물",
        "ticker": "148070.KS", "sector": "미국/국채ETF", "market": "KR_ETF",
        "aliases": ["tiger미국채10년", "미국채etf", "148070"],
    },
    "130680.KS": {
        "standard_name": "TIGER 단기채권액티브",
        "ticker": "130680.KS", "sector": "국내/단기채ETF", "market": "KR_ETF",
        "aliases": ["tiger단기채권", "tiger단기채", "단기채etf", "130680"],
    },
    "273130.KS": {
        "standard_name": "KODEX 단기채권",
        "ticker": "273130.KS", "sector": "국내/단기채ETF", "market": "KR_ETF",
        "aliases": ["kodex단기채권", "kodex단기채", "273130"],
    },
    "190160.KS": {
        "standard_name": "TIGER 단기통안채",
        "ticker": "190160.KS", "sector": "국내/초단기채ETF", "market": "KR_ETF",
        "aliases": ["tiger단기통안채", "통안채etf", "190160"],
    },
    "232080.KS": {
        "standard_name": "TIGER 200 IT",
        "ticker": "232080.KS", "sector": "국내/IT섹터ETF", "market": "KR_ETF",
        "aliases": ["tiger200it", "tigertit", "232080"],
    },

    # ── 미국 대형주 ────────────────────────────────────────────
    "AAPL": {
        "standard_name": "Apple Inc.",
        "ticker": "AAPL", "sector": "미국/IT", "market": "US",
        "aliases": ["aapl", "애플", "apple", "아이폰회사"],
    },
    "MSFT": {
        "standard_name": "Microsoft Corp.",
        "ticker": "MSFT", "sector": "미국/IT", "market": "US",
        "aliases": ["msft", "마이크로소프트", "microsoft", "마소"],
    },
    "NVDA": {
        "standard_name": "NVIDIA Corp.",
        "ticker": "NVDA", "sector": "미국/반도체", "market": "US",
        "aliases": ["nvda", "엔비디아", "nvidia"],
    },
    "TSLA": {
        "standard_name": "Tesla Inc.",
        "ticker": "TSLA", "sector": "미국/전기차", "market": "US",
        "aliases": ["tsla", "테슬라", "tesla"],
    },
    "GOOGL": {
        "standard_name": "Alphabet Inc. (Google)",
        "ticker": "GOOGL", "sector": "미국/IT", "market": "US",
        "aliases": ["googl", "구글", "google", "알파벳", "alphabet"],
    },
    "META": {
        "standard_name": "Meta Platforms Inc.",
        "ticker": "META", "sector": "미국/IT", "market": "US",
        "aliases": ["meta", "메타", "facebook", "페이스북"],
    },
    "AMZN": {
        "standard_name": "Amazon.com Inc.",
        "ticker": "AMZN", "sector": "미국/IT/이커머스", "market": "US",
        "aliases": ["amzn", "아마존", "amazon"],
    },
    "PLTR": {
        "standard_name": "Palantir Technologies",
        "ticker": "PLTR", "sector": "미국/AI/빅데이터", "market": "US",
        "aliases": ["pltr", "팔란티어", "palantir"],
    },
    "AMD": {
        "standard_name": "Advanced Micro Devices",
        "ticker": "AMD", "sector": "미국/반도체", "market": "US",
        "aliases": ["amd", "에이엠디"],
    },
    "AVGO": {
        "standard_name": "Broadcom Inc.",
        "ticker": "AVGO", "sector": "미국/반도체", "market": "US",
        "aliases": ["avgo", "브로드컴", "broadcom"],
    },
    "ASML": {
        "standard_name": "ASML Holding NV",
        "ticker": "ASML", "sector": "반도체장비", "market": "US",
        "aliases": ["asml", "에이에스엠엘"],
    },
    "TSM": {
        "standard_name": "Taiwan Semiconductor (TSMC)",
        "ticker": "TSM", "sector": "반도체", "market": "US",
        "aliases": ["tsm", "tsmc", "대만tsmc", "대만반도체"],
    },
    "JPM": {
        "standard_name": "JPMorgan Chase",
        "ticker": "JPM", "sector": "미국/금융/은행", "market": "US",
        "aliases": ["jpm", "jp모건", "jpmorgan"],
    },
    "V": {
        "standard_name": "Visa Inc.",
        "ticker": "V", "sector": "미국/금융/결제", "market": "US",
        "aliases": ["v", "비자", "visa"],
    },
    "MA": {
        "standard_name": "Mastercard Inc.",
        "ticker": "MA", "sector": "미국/금융/결제", "market": "US",
        "aliases": ["ma", "마스터카드", "mastercard"],
    },
    "BRK-B": {
        "standard_name": "Berkshire Hathaway B",
        "ticker": "BRK-B", "sector": "미국/금융/지주", "market": "US",
        "aliases": ["brkb", "brk-b", "brk.b", "버크셔해서웨이", "버크셔"],
    },

    # ── 미국 ETF ───────────────────────────────────────────────
    "SPY": {
        "standard_name": "SPDR S&P 500 ETF",
        "ticker": "SPY", "sector": "미국/S&P500ETF", "market": "US_ETF",
        "aliases": ["spy", "s&p500etf", "sp500", "spdr"],
    },
    "QQQ": {
        "standard_name": "Invesco QQQ Trust (Nasdaq 100)",
        "ticker": "QQQ", "sector": "미국/나스닥100ETF", "market": "US_ETF",
        "aliases": ["qqq", "나스닥100", "nasdaq100", "나스닥100etf", "큐큐큐"],
    },
    "VOO": {
        "standard_name": "Vanguard S&P 500 ETF",
        "ticker": "VOO", "sector": "미국/S&P500ETF", "market": "US_ETF",
        "aliases": ["voo", "뱅가드s&p500", "vanguardsp500"],
    },
    "VTI": {
        "standard_name": "Vanguard Total Stock Market ETF",
        "ticker": "VTI", "sector": "미국/전체시장ETF", "market": "US_ETF",
        "aliases": ["vti", "뱅가드total", "미국전체주식"],
    },
    "SCHD": {
        "standard_name": "Schwab US Dividend Equity ETF",
        "ticker": "SCHD", "sector": "미국/배당ETF", "market": "US_ETF",
        "aliases": ["schd", "슈왑배당", "미국배당etf"],
    },
    "GLD": {
        "standard_name": "SPDR Gold Shares",
        "ticker": "GLD", "sector": "금/원자재ETF", "market": "US_ETF",
        "aliases": ["gld", "금etf", "goldetf", "골드etf"],
    },
    "TLT": {
        "standard_name": "iShares 20+ Year Treasury Bond ETF",
        "ticker": "TLT", "sector": "미국/장기국채ETF", "market": "US_ETF",
        "aliases": ["tlt", "미국장기국채etf", "미국채20년"],
    },
    "IEF": {
        "standard_name": "iShares 7-10 Year Treasury Bond ETF",
        "ticker": "IEF", "sector": "미국/중기국채ETF", "market": "US_ETF",
        "aliases": ["ief", "미국중기국채etf"],
    },
    "AGG": {
        "standard_name": "iShares Core US Aggregate Bond ETF",
        "ticker": "AGG", "sector": "미국/채권ETF", "market": "US_ETF",
        "aliases": ["agg", "미국채권etf", "채권종합etf"],
    },
    "VWO": {
        "standard_name": "Vanguard FTSE Emerging Markets ETF",
        "ticker": "VWO", "sector": "신흥국/주식ETF", "market": "US_ETF",
        "aliases": ["vwo", "신흥국etf", "이머징etf"],
    },
}


# ═══════════════════════════════════════════════════════════
# 인덱스 빌드 (모듈 로드 시 1회 실행)
# ═══════════════════════════════════════════════════════════

def _normalize_key(s: str) -> str:
    """소문자 + 공백·특수문자 제거 → 매칭 키"""
    return s.lower().replace(" ", "").replace("-", "").replace(".", "").replace("&", "")


# alias_normalized → ticker_key
_ALIAS_INDEX: dict[str, str] = {}
# (alias_normalized, ticker_key) 리스트 — 퍼지 매칭용
_FUZZY_CORPUS: list[tuple[str, str]] = []

for _tk, _info in TICKER_DICT.items():
    for _alias in _info["aliases"]:
        _nk = _normalize_key(_alias)
        _ALIAS_INDEX[_nk] = _tk
        _FUZZY_CORPUS.append((_nk, _tk))


# ═══════════════════════════════════════════════════════════
# 매칭 함수
# ═══════════════════════════════════════════════════════════

def _exact_match(normalized: str) -> str | None:
    """정적 사전 alias 정확 매칭. 매칭된 ticker_key 반환."""
    return _ALIAS_INDEX.get(normalized)


def _fuzzy_match(normalized: str) -> tuple[str | None, int]:
    """
    TheFuzz를 이용한 퍼지 매칭.
    반환: (ticker_key or None, confidence_score 0-100)
    TheFuzz 미설치 시 (None, 0) 반환.
    """
    try:
        from thefuzz import process, fuzz  # type: ignore
    except ImportError:
        return None, 0

    candidates = [alias for alias, _ in _FUZZY_CORPUS]
    result = process.extractOne(
        normalized,
        candidates,
        scorer=fuzz.token_set_ratio,
    )
    if not result:
        return None, 0

    best_alias, score = result[0], result[1]
    if score >= FUZZY_THRESHOLD_LOW:
        ticker_key = _ALIAS_INDEX.get(best_alias)
        return ticker_key, score

    return None, 0


# ═══════════════════════════════════════════════════════════
# 단일 항목 정규화
# ═══════════════════════════════════════════════════════════

def enrich_one(investment: dict) -> dict:
    """
    { "name": "삼전", "amount": 5000000 }
    →
    { "raw_name": "삼전", "standard_name": "삼성전자",
      "ticker": "005930.KS", "sector": "IT/반도체", "market": "KR",
      "amount": 5000000, "match_type": "alias_exact", "match_confidence": 100 }

    이미 정규화된 항목(alias_exact / fuzzy)은 재처리하지 않는다.
    → normalize를 여러 번 실행해도 데이터가 덮어써지지 않음.
    unresolved 항목은 재시도한다 (사전 업데이트 후 재실행 가능).
    """
    # ── 재실행 안전 처리: 이미 확정된 항목 스킵 ──────────────
    if ("raw_name" in investment
            and investment.get("match_type") in ("alias_exact", "fuzzy", "manual")
            and investment.get("ticker") is not None):
        return investment

    # raw_name이 있지만 unresolved → raw_name을 name으로 재사용해 재시도
    raw = investment.get("name") or investment.get("raw_name", "")
    normalized = _normalize_key(raw)
    result = dict(investment)
    result["raw_name"] = raw

    # 1. 정확 매칭
    tk = _exact_match(normalized)
    if tk:
        info = TICKER_DICT[tk]
        result.update({
            "standard_name":    info["standard_name"],
            "ticker":           info["ticker"],
            "sector":           info["sector"],
            "market":           info["market"],
            "match_type":       "alias_exact",
            "match_confidence": 100,
        })
        result.pop("name", None)
        return result

    # 2. 퍼지 매칭
    tk_fuzzy, score = _fuzzy_match(normalized)
    if tk_fuzzy and score >= FUZZY_THRESHOLD_LOW:
        info = TICKER_DICT[tk_fuzzy]
        result.update({
            "standard_name":    info["standard_name"],
            "ticker":           info["ticker"],
            "sector":           info["sector"],
            "market":           info["market"],
            "match_type":       "fuzzy",
            "match_confidence": score,
        })
        result.pop("name", None)
        return result

    # 2-B. KIS API 종목명 검색 (정적 사전·퍼지 실패 시 최종 fallback)
    try:
        import sys as _sys
        _kis_dir = str(Path(__file__).parent)
        if _kis_dir not in _sys.path:
            _sys.path.insert(0, _kis_dir)
        from kis_client import search_ticker_by_name as _kis_search, is_available as _kis_ok
        if _kis_ok():
            kis_hit = _kis_search(raw)
            if kis_hit and kis_hit.get("ticker"):
                result.update({
                    "standard_name":    kis_hit["standard_name"],
                    "ticker":           kis_hit["ticker"],
                    "sector":           "국내ETF",
                    "market":           "KR_ETF",
                    "match_type":       "kis_api",
                    "match_confidence": 90,
                })
                result.pop("name", None)
                return result
    except Exception:
        pass

    # 3. 미매칭
    result.update({
        "standard_name":    raw,    # 원본 유지
        "ticker":           None,
        "sector":           "미분류",
        "market":           "unknown",
        "match_type":       "unresolved",
        "match_confidence": score if score else 0,
    })
    result.pop("name", None)
    return result


# ═══════════════════════════════════════════════════════════
# kyc.json 전체 처리
# ═══════════════════════════════════════════════════════════

def normalize_kyc(client_id: str) -> None:
    kyc_path = BASE_DIR / "data" / "clients" / client_id / "kyc.json"

    if not kyc_path.exists():
        print(f"[오류] kyc.json 없음: {kyc_path}")
        sys.exit(1)

    kyc = json.loads(kyc_path.read_text(encoding="utf-8"))

    raw_investments: list[dict] = kyc.get("assets", {}).get("investments", [])
    if not raw_investments:
        print(f"[{client_id}] investments 배열 없음 — 정규화 건너뜀")
        return

    enriched: list[dict] = []
    unresolved_count = 0
    low_confidence_count = 0

    for inv in raw_investments:
        result = enrich_one(inv)
        # 환헤지 자동 감지 — 상품명에 (H)/헤지/Hedge 패턴이면 fx_hedged=True
        if result.get("fx_hedged") is None:
            import re as _re
            combined = " ".join([
                result.get("standard_name", ""),
                result.get("raw_name", ""),
                result.get("name", ""),
            ]).upper()
            if _re.search(r"\(H\)|헤지|HEDGE|\(환헤지\)", combined):
                result["fx_hedged"] = True
            elif _re.search(r"\(UH\)|환노출|UNHEDGE", combined):
                result["fx_hedged"] = False
            # None 유지 → 스트레스 테스트에서 환노출로 처리
        enriched.append(result)

        mt    = result["match_type"]
        score = result["match_confidence"]
        name  = result.get("raw_name", "")

        if mt == "unresolved":
            unresolved_count += 1
            print(f"  ⚠ [{client_id}] '{name}' → unresolved (신뢰도 {score}%)")
        elif mt == "fuzzy" and score < FUZZY_THRESHOLD_HIGH:
            low_confidence_count += 1
            std = result.get("standard_name", "")
            print(f"  △ [{client_id}] '{name}' → '{std}' (퍼지 {score}%, 저신뢰)")
        else:
            std = result.get("standard_name", "")
            tk  = result.get("ticker", "")
            print(f"  ✓ [{client_id}] '{name}' → {std} ({tk})")

    # kyc.json 업데이트
    kyc["assets"]["investments"] = enriched

    # needs_review / ticker_normalization 플래그 갱신
    # ticker_normalization은 KYCStatus 스키마에 Optional[str]로 정의되어 있으므로
    # Pydantic 검증 시 유실되지 않는다.
    if unresolved_count > 0 or low_confidence_count > 0:
        kyc.setdefault("status", {})["needs_review"] = True
        kyc["status"]["ticker_normalization"] = (
            f"unresolved {unresolved_count}개 / 저신뢰 {low_confidence_count}개"
        )
        print(f"  → needs_review: true 설정 "
              f"(unresolved={unresolved_count}, low_conf={low_confidence_count})")
    else:
        kyc.setdefault("status", {})["ticker_normalization"] = "all_resolved"

    safe_write_json(kyc_path, kyc)
    print(f"[OK] {client_id} kyc.json 정규화 완료 "
          f"({len(enriched)}개 종목 처리)")


# ═══════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python scripts/ticker_normalizer.py <client_id>")
        print("예)    python scripts/ticker_normalizer.py client_20260527_001")
        sys.exit(1)

    cid = sys.argv[1]
    print(f"[ticker_normalizer] {cid} 종목 정규화 시작")
    normalize_kyc(cid)
