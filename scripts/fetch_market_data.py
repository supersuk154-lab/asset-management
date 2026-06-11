"""
안티그래비티 HTML 리포트 → market_data/ 동기화 스크립트

매일 생성되는 투자리포트_YYYYMMDD_HHMM.html을 파싱해서
trending_stocks.json과 macro_snapshot.json을 자동 생성한다.

필요 패키지:
  pip install beautifulsoup4

사용법:
  python scripts/fetch_market_data.py           # 오늘 데이터 없으면 파싱
  python scripts/fetch_market_data.py --force   # 오늘 데이터 있어도 강제 재파싱
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils import safe_write_json

BASE_DIR = Path(__file__).parent.parent
MARKET_DIR = BASE_DIR / "market_data"

# 안티그래비티 리포트 폴더 경로
# EXE 버전, 일반 버전 구분 없이 여기에 복사해두면 자동 파싱
REPORTS_DIR = BASE_DIR / "market_data" / "source_reports"

LOCAL_TRENDING = MARKET_DIR / "trending_stocks.json"
LOCAL_MACRO    = MARKET_DIR / "macro_snapshot.json"


# ─────────────────────────────────────
# 유틸
# ─────────────────────────────────────

def find_latest_report() -> Path | None:
    """가장 최신 투자리포트_*.html 반환"""
    files = sorted(REPORTS_DIR.glob("투자리포트_*.html"), reverse=True)
    return files[0] if files else None


def find_recent_reports(days: int = 7) -> list:
    """최근 N일 내 투자리포트_*.html 파일 목록 [(Path, date_str), ...] 최신순"""
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for f in sorted(REPORTS_DIR.glob("투자리포트_*.html"), reverse=True):
        date_str = report_date_from_filename(f)
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                result.append((f, date_str))
        except Exception:
            pass
    return result


def aggregate_stocks(daily_lists: list) -> list:
    """
    여러 날짜의 종목 목록을 집계하여 mention_streak·source_confidence를 계산한다.
    daily_lists: [(date_str, stocks_list), ...] 최신순

    추가 필드:
      mention_streak     — 현재 날짜 기준 연속으로 언급된 일수 (모멘텀 지속성)
      source_confidence  — 전체 파일 중 언급된 파일 비율 (0.0~1.0)
      days_mentioned     — 언급된 날짜 수 (연속 아니어도 포함)
    """
    from collections import defaultdict

    total_days = len(daily_lists)
    if total_days == 0:
        return []

    accumulated: dict[str, dict] = {}
    date_sets: dict[str, set] = defaultdict(set)

    for date_str, stocks in daily_lists:
        for stock in stocks:
            name = stock["name"]
            date_sets[date_str].add(name)

            if name not in accumulated:
                # 첫 등장 = 가장 최신 데이터 (daily_lists가 최신순이므로)
                accumulated[name] = {
                    "name":           name,
                    "mention_count":  0,
                    "total_channels": stock.get("total_channels", 0),
                    "opinion":        stock.get("opinion", "중립"),
                    "source_files":   0,
                }
            accumulated[name]["mention_count"] += stock.get("mention_count", 0)
            accumulated[name]["source_files"]  += 1

    # mention_streak: 가장 최근 날짜부터 연속 언급 일수
    sorted_dates = sorted(date_sets.keys(), reverse=True)
    for name, entry in accumulated.items():
        streak = 0
        for i, date in enumerate(sorted_dates):
            if name not in date_sets[date]:
                break  # 연속 끊김
            if i == 0:
                streak = 1
            else:
                prev = sorted_dates[i - 1]
                gap  = (datetime.strptime(prev, "%Y-%m-%d") -
                        datetime.strptime(date, "%Y-%m-%d")).days
                if gap == 1:
                    streak += 1
                else:
                    break

        files_mentioning = entry["source_files"]
        n_channels       = entry["total_channels"] or 1
        entry["mention_streak"]    = streak
        entry["source_confidence"] = round(files_mentioning / total_days, 2)
        entry["days_mentioned"]    = sum(1 for d in sorted_dates if name in date_sets[d])
        entry["mention_ratio"]     = round(
            entry["mention_count"] / files_mentioning / n_channels, 2
        ) if files_mentioning > 0 else 0

    # 정렬: mention_streak 우선, 동률은 mention_count
    return sorted(accumulated.values(),
                  key=lambda x: (x["mention_streak"], x["mention_count"]),
                  reverse=True)


def report_date_from_filename(path: Path) -> str:
    """투자리포트_20260527_0658.html → '2026-05-27'"""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", path.name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.now().strftime("%Y-%m-%d")


def already_synced_today() -> bool:
    """오늘 날짜로 macro_snapshot이 이미 존재하면 True"""
    if not LOCAL_MACRO.exists():
        return False
    try:
        data = json.loads(LOCAL_MACRO.read_text(encoding="utf-8"))
        return data.get("date") == datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return False


# ─────────────────────────────────────
# 파서
# ─────────────────────────────────────

def parse_trending_stocks(soup) -> list:
    """
    멀티채널 급증 언급 종목 테이블 (id="surge-alert-card") 파싱
    → [{"name", "mention_count", "total_channels", "mention_ratio", "opinion"}, ...]
    """
    card = soup.find("div", id="surge-alert-card")
    if not card:
        print("  [경고] surge-alert-card 없음")
        return []

    stocks = []
    rows = card.select("tbody tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        name         = cells[0].get_text(strip=True)
        channel_text = cells[1].get_text(strip=True)   # "6곳 중 4곳"
        opinion      = cells[2].get_text(strip=True)   # "강세" / "중립" / "약세"

        m = re.search(r"(\d+)곳 중 (\d+)곳", channel_text)
        total   = int(m.group(1)) if m else 0
        mention = int(m.group(2)) if m else 0

        stocks.append({
            "name":          name,
            "mention_count": mention,
            "total_channels": total,
            "mention_ratio": round(mention / total, 2) if total else 0,
            "opinion":       opinion,   # "강세" / "중립" / "약세"
        })

    return stocks


def parse_fear_greed(soup) -> dict:
    """
    CNN 공포·탐욕 지수 (id="fear-greed-card") 파싱
    → {"value": 60.8, "label": "탐욕"}
    """
    card = soup.find("div", id="fear-greed-card")
    if not card:
        return {"value": None, "label": ""}

    # "60.8 — 탐욕" 형식의 굵은 p 태그
    for p in card.find_all("p"):
        text = p.get_text(strip=True)
        m = re.search(r"([\d.]+)\s*[—\-–]\s*(.+)", text)
        if m:
            return {"value": float(m.group(1)), "label": m.group(2).strip()}

    return {"value": None, "label": ""}


def parse_heatmap(soup) -> dict:
    """
    시장 감성 히트맵 파싱
    → {"sectors": [{"name", "sentiment"}, ...], "stocks": [...]}
    """
    result = {"sectors": [], "stocks": []}

    # heatmap-grid가 포함된 카드 직접 탐색 (h2 텍스트에 의존 안 함)
    heatmap_card = None
    for card in soup.find_all("div", class_="card"):
        if card.find("div", class_="heatmap-grid"):
            heatmap_card = card
            break

    if not heatmap_card:
        print("  [경고] 히트맵 카드 없음 (이 리포트에 히트맵 섹션이 없을 수 있음)")
        return result

    grids = heatmap_card.find_all("div", class_="heatmap-grid")
    for i, grid in enumerate(grids):
        target = result["sectors"] if i == 0 else result["stocks"]
        for item in grid.find_all("div", class_="heatmap-item"):
            classes = item.get("class", [])
            if "heatmap-bullish" in classes:
                sentiment = "강세"
            elif "heatmap-bearish" in classes:
                sentiment = "약세"
            else:
                sentiment = "중립"

            # <span> 서브텍스트 제거 후 이름 추출
            span = item.find("span")
            if span:
                span.decompose()
            name = item.get_text(strip=True)

            target.append({"name": name, "sentiment": sentiment})

    return result


def parse_short_summary(soup) -> str:
    """
    SHORT 모드 핵심 요약 텍스트 파싱
    → macro-analyst가 초보자용으로 재가공할 원문
    """
    for card in soup.find_all("div", class_="card"):
        if card.find("div", class_="mode-short"):
            p = card.find("p")
            if p:
                return p.get_text(strip=True)
    return ""


def parse_stock_prices(soup) -> list:
    """
    언급 종목 실시간 주가 테이블 (id="stock-price-card") 파싱
    → [{"name", "ticker", "price", "change", "direction"}, ...]
    """
    card = soup.find("div", id="stock-price-card")
    if not card:
        return []

    prices = []
    rows = card.select("tbody tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        change_td = cells[3]
        td_style  = change_td.get("style", "")
        direction = "up" if "#e53e3e" in td_style else "down"

        prices.append({
            "name":      cells[0].get_text(strip=True),
            "ticker":    cells[1].get_text(strip=True),
            "price":     cells[2].get_text(strip=True),
            "change":    change_td.get_text(strip=True),
            "direction": direction,
        })

    return prices


# ─────────────────────────────────────
# 저장
# ─────────────────────────────────────

def fear_greed_to_sentiment(value) -> str:
    """공포탐욕 수치 → market_sentiment 문자열"""
    if value is None:
        return "불확실"
    if value >= 75:
        return "강세장"
    elif value >= 45:
        return "중립"
    elif value >= 25:
        return "약세장"
    else:
        return "극도의 공포"


def save_trending(stocks: list, date: str, source_file: str,
                  source_label: str = "antigravity_html"):
    MARKET_DIR.mkdir(exist_ok=True)
    data = {
        "date":        date,
        "source":      source_label,
        "source_file": source_file,
        "stocks":      stocks,
    }
    safe_write_json(LOCAL_TRENDING, data)
    print(f"  [OK] trending_stocks.json: {len(stocks)}개 종목")


def save_macro(fear_greed: dict, heatmap: dict, short_summary: str,
               date: str, source_file: str):
    MARKET_DIR.mkdir(exist_ok=True)
    fg_value   = fear_greed.get("value")
    sentiment  = fear_greed_to_sentiment(fg_value)

    bullish_sectors = [s["name"] for s in heatmap.get("sectors", []) if s["sentiment"] == "강세"]
    bearish_sectors = [s["name"] for s in heatmap.get("sectors", []) if s["sentiment"] == "약세"]

    data = {
        "date":               date,
        "source":             "antigravity_html",
        "source_file":        source_file,
        "fear_greed_index":   fg_value,
        "fear_greed_label":   fear_greed.get("label", ""),
        "market_sentiment":   sentiment,
        "bullish_sectors":    bullish_sectors,
        "bearish_sectors":    bearish_sectors,
        "heatmap_sectors":    heatmap.get("sectors", []),
        "heatmap_stocks":     heatmap.get("stocks", []),
        # macro-analyst가 초보자용으로 재가공할 원문
        "short_summary_raw":  short_summary,
        # macro-analyst가 채워주는 필드 (초기엔 비워둠)
        "summary_for_beginner": "",
        "interest_rate_trend":  "",
        "inflation_status":     "",
    }
    safe_write_json(LOCAL_MACRO, data)
    print(f"  [OK] macro_snapshot.json: {sentiment} / 공포탐욕 {fg_value} ({fear_greed.get('label', '')})")
    if bullish_sectors:
        print(f"       강세 섹터: {', '.join(bullish_sectors)}")


# ─────────────────────────────────────
# 메인
# ─────────────────────────────────────

def sync(force: bool = False):
    if not force and already_synced_today():
        print(f"[스킵] 오늘({datetime.now().strftime('%Y-%m-%d')}) 데이터 이미 존재. --force로 강제 실행 가능.")
        return

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 최근 7일 HTML 파일 수집 (멀티소스 집계)
    recent_reports = find_recent_reports(days=7)

    if not recent_reports:
        print(f"[Step 0-A 건너뜀] 안티그래비티 리포트 파일 없음")
        print(f"  경로: {REPORTS_DIR}")
        print(f"  조치: '투자리포트_YYYYMMDD_HHMM.html' 파일을 위 경로에 복사하세요.")
        print(f"  → macro-analyst가 일반 원칙으로 시장 국면을 대체합니다.")
        return

    print(f"[파싱 시작] {len(recent_reports)}개 파일 처리 (최근 7일)")

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[오류] beautifulsoup4 없음: pip install beautifulsoup4")
        return

    # 모든 파일 파싱 (종목 집계용)
    daily_lists = []
    for html_path, date_str in recent_reports:
        print(f"  파싱: {html_path.name}")
        try:
            with open(html_path, encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            stocks = parse_trending_stocks(soup)
            daily_lists.append((date_str, stocks))
        except Exception as e:
            print(f"  [경고] {html_path.name} 파싱 실패: {e}")

    if not daily_lists:
        print("[오류] 파싱 가능한 파일 없음")
        return

    # 최신 파일에서 macro 데이터 추출 — 각 파서는 독립적으로 실패
    latest_path, latest_date = recent_reports[0]
    with open(latest_path, encoding="utf-8") as f:
        soup_latest = BeautifulSoup(f.read(), "html.parser")

    fear_greed    = None
    heatmap       = None
    short_summary = None

    try:
        fear_greed = parse_fear_greed(soup_latest)
    except Exception as e:
        print(f"  [경고] fear_greed 파싱 실패 (다른 데이터는 정상 수집): {e}")

    try:
        heatmap = parse_heatmap(soup_latest)
    except Exception as e:
        print(f"  [경고] heatmap 파싱 실패 — HTML 구조 변경 가능성 확인 필요: {e}")

    try:
        short_summary = parse_short_summary(soup_latest)
    except Exception as e:
        print(f"  [경고] short_summary 파싱 실패: {e}")

    # 종목 집계: 단일 파일이면 그대로, 복수이면 멀티소스 집계
    if len(daily_lists) > 1:
        aggregated_stocks = aggregate_stocks(daily_lists)
        source_label = f"antigravity_html_aggregated_{len(daily_lists)}days"
        print(f"  [집계] {len(daily_lists)}일치 → {len(aggregated_stocks)}개 종목 / mention_streak 계산 완료")
    else:
        aggregated_stocks = daily_lists[0][1]
        source_label = "antigravity_html"

    save_trending(aggregated_stocks, latest_date, latest_path.name, source_label)
    save_macro(fear_greed, heatmap, short_summary, latest_date, latest_path.name)

    print(f"\n[완료] {len(recent_reports)}개 파일 → market_data/ 동기화 성공")


if __name__ == "__main__":
    force = "--force" in sys.argv
    sync(force=force)
