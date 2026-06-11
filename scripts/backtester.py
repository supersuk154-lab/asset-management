"""
backtester.py — 추천 종목 성과 추적 (사후 검증)

history.json의 trending_snapshot에 저장된 종목들을 yfinance로 조회하여
추천 시점 대비 현재 수익률을 계산한다.

저장 구조:
  history.json.sessions[].trending_snapshot  ← finalize 시 자동 저장
  → {ticker, name, price_at_recommendation, date}

출력:
  data/clients/{id}/backtest_report.json  ← 고객별 사후 검증
  market_data/backtest_summary.json       ← 전체 시스템 정확도 요약

사용법:
  python scripts/backtester.py                     # 전체 고객 백테스트
  python scripts/backtester.py --client client_xxx # 특정 고객만
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from utils import safe_write_json
import price_cache as _pc

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
MARKET_DIR = BASE_DIR / "market_data"
CLIENTS_DIR = DATA_DIR / "clients"
SUMMARY_OUT = MARKET_DIR / "backtest_summary.json"

TODAY = datetime.now().strftime("%Y-%m-%d")

# 기준 벤치마크 티커
BENCHMARK = {
    "KOSPI":   "^KS11",
    "S&P500":  "^GSPC",
}


def get_price_at_date(ticker: str, date_str: str) -> float | None:
    """특정 날짜 직후 첫 거래일 종가 반환.
    price_cache 우선 조회 → miss 시 yfinance 직접 호출로 폴백.
    """
    # 1차: price_cache에서 date_str 이후 가장 가까운 날짜 가격 반환
    try:
        cache = _pc.load()
        close_dict = _pc.get_close_series(ticker, cache)
        if close_dict:
            dates = sorted(d for d in close_dict if d >= date_str)
            if dates:
                return round(close_dict[dates[0]], 4)
    except Exception:
        pass

    # 2차: price_cache miss → yfinance 직접 조회 (오래된 추천일 대응)
    try:
        import yfinance as yf
        from datetime import timedelta

        start = datetime.strptime(date_str, "%Y-%m-%d")
        end   = start + timedelta(days=10)
        hist  = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )
        close = hist["Close"].dropna()
        if not close.empty:
            return round(float(close.iloc[0]), 4)
    except Exception:
        pass
    return None


def get_current_price(ticker: str) -> float | None:
    """price_cache 경유 — 스테일 시 ensure_cached 가 yfinance 재수집."""
    cache = _pc.ensure_cached([ticker])
    close_dict = _pc.get_close_series(ticker, cache)
    if close_dict:
        try:
            return round(list(close_dict.values())[-1], 4)
        except Exception:
            pass
    return None


def calc_return(price_start: float, price_now: float) -> float:
    return round((price_now - price_start) / price_start * 100, 2)


def backtest_client(client_id: str) -> dict | None:
    client_dir   = CLIENTS_DIR / client_id
    history_path = client_dir / "history.json"

    if not history_path.exists():
        return None

    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    sessions = history.get("sessions", [])
    results  = []

    for session in sessions:
        session_date = session.get("date", "")
        snapshot     = session.get("trending_snapshot", [])

        if not snapshot:
            results.append({
                "session_date": session_date,
                "note": "trending_snapshot 없음 — 이 세션 이전에 finalize된 기록",
                "picks": [],
            })
            continue

        # 경과 일수
        try:
            days_elapsed = (datetime.strptime(TODAY, "%Y-%m-%d") -
                            datetime.strptime(session_date, "%Y-%m-%d")).days
        except Exception:
            days_elapsed = 0

        if days_elapsed < 7:
            results.append({
                "session_date": session_date,
                "note": f"추천 후 {days_elapsed}일 경과 — 최소 7일 이후 의미 있는 성과 측정 가능",
                "picks": [],
            })
            continue

        # 벤치마크 성과
        benchmarks = {}
        for bname, bticker in BENCHMARK.items():
            p_start = get_price_at_date(bticker, session_date)
            p_now   = get_current_price(bticker)
            if p_start and p_now:
                benchmarks[bname] = {
                    "ticker":       bticker,
                    "price_start":  p_start,
                    "price_now":    p_now,
                    "return_pct":   calc_return(p_start, p_now),
                }

        # 개별 종목 성과
        pick_results = []
        for snap in snapshot:
            ticker    = snap.get("ticker")
            name      = snap.get("name", ticker)
            p_at_rec  = snap.get("price_at_recommendation")

            if not ticker or not p_at_rec:
                continue

            p_now = get_current_price(ticker)
            if p_now is None:
                pick_results.append({
                    "ticker": ticker, "name": name,
                    "note": "현재가 조회 실패 (상장 폐지 또는 티커 오류)",
                })
                continue

            ret = calc_return(p_at_rec, p_now)

            # 벤치마크 대비 초과 수익
            kospi_ret  = benchmarks.get("KOSPI", {}).get("return_pct")
            excess_vs_kospi = round(ret - kospi_ret, 2) if kospi_ret is not None else None

            pick_results.append({
                "ticker":              ticker,
                "name":                name,
                "price_at_recommendation": p_at_rec,
                "price_now":           p_now,
                "return_pct":          ret,
                "excess_vs_kospi":     excess_vs_kospi,
                "beat_benchmark":      (ret > kospi_ret) if kospi_ret is not None else None,
            })
            beat = "✅ 벤치 초과" if (ret > (kospi_ret or 0)) else "❌ 벤치 미달"
            print(f"    {name} ({ticker}): {ret:+.1f}% {beat}")

        # 세션 요약
        if pick_results:
            valid_returns = [p["return_pct"] for p in pick_results if "return_pct" in p]
            avg_return    = round(sum(valid_returns) / len(valid_returns), 2) if valid_returns else None
            beat_count    = sum(1 for p in pick_results if p.get("beat_benchmark"))
            beat_rate     = round(beat_count / len(pick_results) * 100, 1) if pick_results else None
        else:
            avg_return = beat_rate = None

        results.append({
            "session_date":  session_date,
            "days_elapsed":  days_elapsed,
            "benchmarks":    benchmarks,
            "picks":         pick_results,
            "summary": {
                "avg_return_pct": avg_return,
                "beat_rate_pct":  beat_rate,
                "total_picks":    len(pick_results),
            },
        })

    if not results:
        return None

    report = {
        "client_id":     client_id,
        "generated_at":  TODAY,
        "sessions":      results,
    }

    out_path = CLIENTS_DIR / client_id / "backtest_report.json"
    safe_write_json(out_path, report)
    return report


def run(client_filter: str | None = None) -> None:
    print(f"[backtester] {TODAY} 성과 추적 시작\n")

    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("  ⚠ yfinance 없음 → pip install yfinance")
        return

    if not CLIENTS_DIR.exists():
        print("  [INFO] clients/ 폴더 없음")
        return

    client_dirs = sorted(
        d for d in CLIENTS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    if client_filter:
        client_dirs = [d for d in client_dirs if d.name == client_filter]

    all_reports  = []
    beat_total   = 0
    pick_total   = 0

    for cd in client_dirs:
        print(f"  [{cd.name}]")
        report = backtest_client(cd.name)
        if report:
            all_reports.append(report)
            for sess in report["sessions"]:
                summ = sess.get("summary", {})
                beat_rate = summ.get("beat_rate_pct")
                n_picks   = summ.get("total_picks", 0)
                if beat_rate is not None:
                    beat_total += beat_rate * n_picks / 100
                    pick_total += n_picks

    # 전체 요약
    overall_beat_rate = round(beat_total / pick_total * 100, 1) if pick_total > 0 else None
    summary = {
        "generated_at":         TODAY,
        "clients_analyzed":     len(all_reports),
        "total_picks_analyzed": pick_total,
        "overall_beat_rate_pct": overall_beat_rate,
        "note": (
            "beat_rate = 추천 종목 중 KOSPI 수익률을 초과한 비율. "
            "50% 초과이면 시스템이 인덱스보다 우수한 종목 선별 능력을 보임."
        ),
    }
    MARKET_DIR.mkdir(exist_ok=True)
    safe_write_json(SUMMARY_OUT, summary)

    print(f"\n[완료] {len(all_reports)}개 고객 / 총 {pick_total}개 종목 추적")
    if overall_beat_rate is not None:
        grade = "✅ 우수" if overall_beat_rate >= 55 else ("⚠️ 보통" if overall_beat_rate >= 45 else "❌ 미흡")
        print(f"  시스템 벤치마크 초과율: {overall_beat_rate}% {grade}")
    else:
        print("  ※ trending_snapshot이 저장된 세션이 아직 없습니다.")
        print("    이번 finalize부터 자동 저장되므로 7일 이후 재실행하면 결과가 나옵니다.")


if __name__ == "__main__":
    client_arg = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--client" and i + 1 < len(sys.argv) - 1:
            client_arg = sys.argv[i + 2]
    run(client_arg)
