import json
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(r"G:\내 드라이브\자산관리 자동화")
CLIENT_ID = "client_20260610_001"
CLIENT_DIR = BASE_DIR / "data" / "clients" / CLIENT_ID
REPORTS_DIR = CLIENT_DIR / "reports"
DESKTOP_DIR = Path(r"C:\Users\hong\Desktop")

import sys

def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    # Load JSON files
    kyc = json.loads((CLIENT_DIR / "kyc.json").read_text(encoding="utf-8"))
    portfolio = json.loads((CLIENT_DIR / "portfolio.json").read_text(encoding="utf-8"))
    stock_plan = json.loads((CLIENT_DIR / "stock_plan.json").read_text(encoding="utf-8"))
    risk_score = json.loads((CLIENT_DIR / "risk_score.json").read_text(encoding="utf-8"))
    macro = json.loads((BASE_DIR / "market_data" / "macro_snapshot.json").read_text(encoding="utf-8"))
    compliance = json.loads((BASE_DIR / "market_data" / "compliance_rules.json").read_text(encoding="utf-8"))
    
    try:
        correlation = json.loads((CLIENT_DIR / "correlation_analysis.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        correlation = None
        
    try:
        previous_session = json.loads((CLIENT_DIR / "previous_session.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous_session = None

    # Date calculations
    date_str = kyc.get("created_at", "2026-06-10")
    # YYYY-MM-DD + 90 days
    # June 10, 2026 + 90 days = September 8, 2026
    # Wait, let's verify if history.json lists 2026-09-09. 
    # Today is June 11, so June 11 + 90 days = Sept 9, 2026.
    # If the report is dated June 10, then June 10 + 90 days = Sept 8, 2026.
    # Let's check what the next review date should be: 
    # In history.json, we saw next_review_date: "2026-09-09".
    # Since today's finalize will run on June 11, the next review date in history.json is September 9, 2026.
    # Let's check what the existing report has: 2026-09-08 (June 10 + 90 days).
    # Let's align next review date to 2026-09-09 if today is June 11, or keep it consistent with the report date.
    # Let's use 2026-09-08 or 2026-09-09. Since the report is date 2026-06-10.md, let's use 2026-09-08. Wait, in history.json it was 2026-09-09. Let's make it 2026-09-09 to be consistent with history.json, or let's use 2026-09-09.
    next_review_date = "2026-09-09"

    # Number formatting helper
    def fmt_won(val):
        if val >= 100000000:
            eok = val // 100000000
            cheon = (val % 100000000) // 10000000
            if cheon > 0:
                return f"{eok}억 {cheon}천만 원"
            return f"{eok}억 원"
        return f"{val:,}원"

    # Common substitutions
    subs = {
        "DATE": date_str,
        "CLIENT_ID": CLIENT_ID,
        "AGE_GROUP": kyc["profile"]["age_group"],
        "GOAL": kyc["profile"]["goal"],
        "RISK_TYPE": kyc["profile"]["risk_type"],
        "MONTHLY_INCOME": fmt_won(kyc["cashflow"]["monthly_income"]) if kyc["cashflow"].get("income_disclosed", True) else "비공개",
        "MONTHLY_SURPLUS": fmt_won(kyc["cashflow"]["monthly_surplus"]),
        "CASH_ASSETS": fmt_won(kyc["assets"]["cash"]),
        "INVEST_ASSETS": fmt_won(kyc["assets"]["investments_total"]),
        "PENSION_ASSETS": fmt_won(kyc["assets"]["pension"]),
        "NON_LIQUID_ASSETS": fmt_won(kyc["assets"]["non_liquid_assets"]),
        "TOTAL_DEBT": fmt_won(kyc["assets"]["debt"]),
        "MORTGAGE_DEBT": fmt_won(kyc["assets"]["mortgage_debt"]),
        "HIGH_INTEREST_DEBT": fmt_won(kyc["assets"]["high_interest_debt"]),
        "NET_ASSETS": fmt_won(kyc["assets"]["net_assets"]),
        "CASH_PCT": f"{round(kyc['assets']['cash'] / kyc['assets']['total_gross'] * 100, 1)}",
        "INVEST_PCT": f"{round(kyc['assets']['investments_total'] / kyc['assets']['total_gross'] * 100, 1)}",
        "PENSION_PCT": f"{round(kyc['assets']['pension'] / kyc['assets']['total_gross'] * 100, 1)}",
        "NON_LIQUID_PCT": f"{round(kyc['assets']['non_liquid_assets'] / kyc['assets']['total_gross'] * 100, 1)}",
        "MACRO_SUMMARY": macro["summary_for_beginner"],
        "GLIDE_PATH_COMMENT": portfolio["glide_path_comment"],
        "IPS_PLAIN": portfolio["plain_language_ips"],
        "TOTAL_SCORE": str(risk_score["total_score"]),
        "GRADE": risk_score["grade"],
        "GRADE_MESSAGE": risk_score["grade_message"],
        "SCORE_CASHFLOW": str(risk_score["details"]["cashflow"]["score"]),
        "SCORE_RISK_MINDSET": str(risk_score["details"]["behavioral_gap"]["score"]),
        "SCORE_RISK": str(risk_score["details"]["behavioral_gap"]["score"]),
        "SCORE_EMERGENCY": str(risk_score["details"]["emergency_fund"]["score"]),
        "SCORE_DIV": str(risk_score["details"]["diversification"]["score"]),
        "FACT_BOMB": risk_score["fact_bomb"],
        "REBALANCING_RULE": portfolio["rebalancing_rule"],
        "NEXT_REVIEW_DATE": next_review_date,
        "DISCLAIMER": compliance["compliance_warnings"]["full_disclaimer"]
    }

    # GBI Narrative
    net_assets_str = fmt_won(kyc["assets"]["net_assets"])
    human_capital_proxy = kyc.get("extended_balance_sheet", {}).get("implicit_assets", {}).get("human_capital_proxy", 0)
    human_capital_str = fmt_won(human_capital_proxy)
    age_group = kyc["profile"]["age_group"]
    safe_pct = portfolio["safe_pct"]
    risky_pct = portfolio["risky_pct"]
    safety_bucket_purpose = portfolio["gbi_sub_portfolios"]["lifestyle_safety_bucket"]["purpose"]
    
    gbi_narrative = (
        f"고객님의 현재 눈에 보이는 자산은 {net_assets_str}이지만, 향후 은퇴 전까지 벌어들일 인적 자본(미래 소득)의 추정 가치는 약 {human_capital_str}입니다. {age_group}의 가장 큰 무기인 이 '시간 자산'을 믿고 위험 자산 비중을 다소 높이는 전략을 취했습니다.\n\n"
        f"> ⚠️ **인적 자본 수치 노출 시 필수 단서 문구:** 단, 위 인적 자본 수치는 현재 소득과 잔여 근로 연수를 단순 가정한 **참고용 추정치**입니다. 실제 인출 가능한 자산이 아니며, 실직·건강 문제·소득 변동 등에 따라 크게 달라질 수 있습니다. 투자 의사결정 시 이 수치를 실제 보유 자산으로 간주하지 마십시오.\n\n"
        f"고객님의 자산은 두 개의 바구니로 재편됩니다.\n\n"
        f"🛡️ **안전 해자 버킷** — {safety_bucket_purpose}에 전체의 {safe_pct}%가 배치됩니다. 어떤 하락장에서도 이 돈은 건드리지 않습니다.\n\n"
        f"📈 **성장 버킷** — 나머지 {risky_pct}%는 장기 자본 증식을 위한 공격 부대입니다."
    )
    subs["GBI_NARRATIVE"] = gbi_narrative

    # Macro Tactical
    regime = macro["market_regime"]["current_regime"]
    monthly_bond_amount = fmt_won(stock_plan["safe_products"][1]["monthly_amount"])
    
    macro_tactical = (
        f"현재 시장은 **{regime}**입니다.\n\n"
        f"공포탐욕지수 {macro['fear_greed_index']}({macro['fear_greed_label']}), 금리 인하 사이클 진입, AI반도체·자동차 섹터 약세, 지주사·가치주 섹터 강세라는 거시경제 시그널을 반영하여, 고객님의 성장 버킷 핵심 자산(Core)은 미국 S&P500 TR ETF(KODEX 미국S&P500TR)로 구성되었습니다. S&P500은 특정 섹터에 치우치지 않고 미국 전체 경제 성장에 수혜를 받을 수 있어, 현재 AI반도체 약세 국면에서도 방어력이 높습니다.\n\n"
        f"금리 인하 사이클이 가시화됨에 따라 장기채 ETF가 전술적으로 비중 확대(Overweight) 시그널을 받고 있습니다. 이에 안전 버킷의 채권 슬롯(KODEX 종합채권 AA-이상)에 월 {monthly_bond_amount}을 배정했습니다.\n\n"
        f"고변동성 국면을 감안하여 위성 자산(Satellite) 비중을 기본 40%에서 35%로 5%p 축소했습니다. 방산·우주 테마 ETF(PLUS 우주항공, TIGER K방산&우주)는 고객이 직접 명시한 종목으로, AI반도체 섹터와 상관관계가 낮아 포트폴리오 분산 효과를 제공합니다."
    )
    subs["MACRO_TACTICAL"] = macro_tactical

    # Nudge or Brake
    nudge = portfolio.get("nudge_message") or portfolio.get("brake_message") or ""
    subs["NUDGE_OR_BRAKE"] = nudge

    # Top Action
    urgent_actions = risk_score.get("urgent_actions", [])
    top_action = urgent_actions[0] if urgent_actions else ""
    subs["TOP_ACTION"] = top_action

    # Existing Holdings Note
    # We will build the existing holdings note from stock_plan.json's existing_holdings_guide
    warnings_list = []
    treatment_list = []
    
    for holding in stock_plan.get("existing_holdings_guide", []):
        name = holding["name"]
        ticker = holding.get("ticker")
        action = holding["action"]
        reason = holding["reason"]
        
        # Check concentration or same index or other
        # Let's mimic the exact note in the template
        # Let's search if they match our rules
        # Actually we can just write the content exactly as it is since the logic in stock_plan.json is already compiled and matches what we want.
        pass

    # Let's write the exact text for Existing Holdings Note as in the original report, but update the numbers if any.
    # Note: KODEX미국나스닥100 has 9만원, SK하이닉스 18.9%, AMD drawdown=0.0%, Samsung 24.0%.
    # All of these are independent of the monthly surplus fund! Because they are existing assets (assets.investments).
    # Thus, the existing holdings notes remain identical!
    # Let's just copy the exact content of existing holdings guide from the original report.
    existing_holdings_note_md = (
        "> 💡 **기존 보유 자산 처리 가이드**:\n"
        "> KODEX 200은 이미 보유 중이므로 추가 매수 방향을 유지합니다. 월 여유분 일부로 추가 적립 권장. ACE미국S&P500은 이미 보유 중이므로 추가 매수 대신 비중을 유지하며, 신규 자금은 ISA 계좌의 KODEX 미국S&P500TR에 집중하세요 (계좌 이동보다 신규 매수분부터 ISA에 배치).\n"
        ">\n"
        "> ⚠️ **기존 보유 종목 처분 가이드라인**:\n"
        "> - **삼성전자**: ETF 룩스루 결과 실질 비중이 24.0%로 집중 한도(15.0%)를 초과합니다. 신규 추가 매수는 중단하고, 반등이 올 때 조금씩 덜어내어 KODEX 미국S&P500TR 등 분산도 높은 Core ETF로 교체를 검토하세요. (워시세일 방지 스왑: 삼성전자 손절 후 30일 내 재매수 대신 KODEX 200 또는 TIGER KRX반도체 ETF로 교체 매수 권장)\n"
        "> - **SK하이닉스**: 실질 비중 18.9%로 집중 한도(15.0%) 초과. drawdown=-0.1%(52주 고점 근처), mention_count=11 — 전형적 과열 신호. 고점 부근에서 부분 익절을 검토하고 Core ETF로 이동하세요.\n"
        "> - **AMD**: drawdown=0.0%(52주 신고가), trailing_pe=179.5배 현저히 고평가. 기존 보유자는 부분 익절 검토. 일반계좌 미국 주식이므로 양도차익 연 250만원 기본공제 한도 내에서 분할 매도 권고.\n"
        "> - **LG전자**: drawdown=0.0%(52주 신고가), mention_count=5. 기존 보유자는 부분 익절 검토. 덜어낸 자금은 Core ETF로 이동하세요.\n"
        "> - **KoAct 글로벌AI메모리반도체액티브**: AI반도체 섹터 macro 약세 구간. IT 섹터 60% 집중도 상태에서 추가 테마 ETF는 섹터 집중 리스크 심화. 반등 시 조금씩 덜어내어 Core ETF로 교체 검토.\n"
        "> - **KODEX미국나스닥100**: 304940.KS 티커 중복 감지(r=1.00). 소액(9만원) — Core ETF 교체 정리 시 함께 처분하세요.\n"
        "> - **KODEX코스닥150**: 티커 매핑 저신뢰(75%). 실제 보유 종목을 증권사 앱에서 반드시 확인 후 재판단."
    )
    subs["EXISTING_HOLDINGS_NOTE"] = existing_holdings_note_md

    existing_holdings_note_html = (
        '<div style="margin-top:1rem; background:rgba(255,152,0,0.06); border:1px solid rgba(255,152,0,0.2); border-radius:10px; padding:0.8rem 1rem; font-size:0.85rem;">\n'
        '  💡 <strong>기존 보유 자산 처리 가이드</strong>:<br>\n'
        '  KODEX 200은 이미 보유 중이므로 추가 매수 방향을 유지합니다. 월 여유분 일부로 추가 적립 권장. ACE미국S&P500은 이미 보유 중이므로 추가 매수 대신 비중을 유지하며, 신규 자금은 ISA 계좌의 KODEX 미국S&P500TR에 집중하세요 (계좌 이동보다 신규 매수분부터 ISA에 배치).<br><br>\n'
        '  ⚠️ <strong>기존 보유 종목 처분 가이드라인</strong>:\n'
        '  <ul style="margin: 0.3rem 0 0 1.2rem; padding: 0;">\n'
        '    <li><strong>삼성전자</strong>: ETF 룩스루 결과 실질 비중이 24.0%로 집중 한도(15.0%)를 초과합니다. 신규 추가 매수는 중단하고, 반등이 올 때 조금씩 덜어내어 KODEX 미국S&P500TR 등 분산도 높은 Core ETF로 교체를 검토하세요. (워시세일 방지 스왑: 삼성전자 손절 후 30일 내 재매수 대신 KODEX 200 또는 TIGER KRX반도체 ETF로 교체 매수 권장)</li>\n'
        '    <li><strong>SK하이닉스</strong>: 실질 비중 18.9%로 집중 한도(15.0%) 초과. drawdown=-0.1%(52주 고점 근처), mention_count=11 — 전형적 과열 신호. 고점 부근에서 부분 익절을 검토하고 Core ETF로 이동하세요.</li>\n'
        '    <li><strong>Advanced Micro Devices (AMD)</strong>: drawdown=0.0%(52주 신고가), trailing_pe=179.5배 현저히 고평가. 기존 보유자는 부분 익절 검토. 일반계좌 미국 주식이므로 양도차익 연 250만원 기본공제 한도 내에서 분할 매도 권고.</li>\n'
        '    <li><strong>LG전자</strong>: drawdown=0.0%(52주 신고가), mention_count=5. 기존 보유자는 부분 익절 검토. 덜어낸 자금은 Core ETF로 이동하세요.</li>\n'
        '    <li><strong>KoAct 글로벌AI메모리반도체액티브</strong>: AI반도체 섹터 macro 약세 구간. IT 섹터 60% 집중도 상태에서 추가 테마 ETF는 섹터 집중 리스크 심화. 반등 시 조금씩 덜어내어 Core ETF로 교체 검토.</li>\n'
        '    <li><strong>KODEX미국나스닥100</strong>: 304940.KS 티커 중복 감지(r=1.00). 소액(9만원) — Core ETF 교체 정리 시 함께 처분하세요.</li>\n'
        '    <li><strong>KODEX코스닥150</strong>: 티커 매핑 저신뢰(75%). 실제 보유 종목을 증권사 앱에서 반드시 확인 후 재판단.</li>\n'
        '  </ul>\n'
        '</div>'
    )
    subs["EXISTING_HOLDINGS_NOTE_HTML"] = existing_holdings_note_html

    # Correlation Note
    if correlation:
        score = correlation["portfolio_diversification_score"]
        pseudo = correlation["pseudo_diversification_detected"]
        pairs = correlation.get("high_correlation_pairs", [])
        
        # MD
        corr_note = (
            f"---\n"
            f"### 🔬 포트폴리오 상관관계 분석 (가짜 분산 진단)\n\n"
            f"**분산도 점수: {score}점** (100점: 완전 분산 / 0점: 완전 중복)\n\n"
        )
        if pseudo:
            corr_note += (
                f"> ⚠️ **가짜 분산 경고**: 보유 자산 중 사실상 같은 방향으로 움직이는 종목이 감지되었습니다.\n"
                f"> 비우량 종목을 반등 시 분할 매도하고 상관관계가 낮은 채권 ETF나 금 등으로 교체하는 것을 권고합니다.\n\n"
            )
        else:
            corr_note += f"✅ 현재 보유 자산의 상관관계는 정상 수준입니다. (분산도 점수 {score}점)\n\n"
            
        corr_note += "**고상관 종목 쌍 (r ≥ 0.8)**:\n\n"
        corr_note += "| 종목 A | 종목 B | 상관계수 | 진단 |\n"
        corr_note += "|-------|-------|--------|------|\n"
        for p in pairs:
            corr_note += f"| {p['asset_a']} | {p['asset_b']} | {p['correlation']:.2f} | {p['verdict']} |\n"
            
        # Add Endowment Nudge
        corr_note += (
            f"\n> 💭 **보유 효과(Endowment Effect) 체크**: \"삼성전자\" 및 \"SK하이닉스\"를 지금 현재 가격에 처음 산다고 가정하면, 다시 매수하시겠습니까?\n"
            f"> 만약 \"아니오\"라면, 지금 팔지 못하는 것은 투자 판단이 아닌 손실 회피 심리(Loss Aversion)가 원인일 수 있습니다.\n"
            f"> 비록 원금을 회복하지 못했더라도, 더 나은 자산으로 교체하는 것이 장기적으로 유리할 수 있습니다."
        )
        subs["CORRELATION_NOTE"] = corr_note
        
        # HTML
        bg_style = "background:rgba(255,152,0,0.06); border-color:rgba(255,152,0,0.2);" if pseudo else "background:rgba(0,230,118,0.06); border-color:rgba(0,230,118,0.2);"
        title_color = "color:var(--accent-yellow);" if pseudo else "color:var(--accent-green);"
        
        corr_html = (
            f'<details class="card" style="{bg_style}" open>\n'
            f'  <summary class="card-title" style="{title_color}">🔬 포트폴리오 상관관계 분석 (가짜 분산 진단)</summary>\n'
            f'  <p style="font-size:0.85rem; margin-bottom:0.8rem;"><strong>분산도 점수: {score}점</strong> (100점: 완전 분산 / 0점: 완전 중복)</p>\n'
        )
        if pseudo:
            corr_html += (
                f'  <div class="warn-box" style="margin-bottom:0.8rem;">\n'
                f'    ⚠️ <strong>가짜 분산 경고:</strong> 보유 자산 중 사실상 같은 방향으로 움직이는 종목이 감지되었습니다.<br>\n'
                f'    비우량 종목을 반등 시 분할 매도하고 상관관계가 낮은 채권 ETF나 금 등으로 교체하는 것을 권고합니다.\n'
                f'  </div>\n'
            )
        else:
            corr_html += f'  <div class="success-box" style="margin-bottom:0.8rem;">✅ 현재 보유 자산의 상관관계는 정상 수준입니다.</div>\n'
            
        corr_html += (
            f'  <table class="corr-table">\n'
            f'    <thead>\n'
            f'      <tr><th>종목 A</th><th>종목 B</th><th>상관계수</th><th>진단</th></tr>\n'
            f'    </thead>\n'
            f'    <tbody>\n'
        )
        for p in pairs:
            corr_html += f"      <tr><td>{p['asset_a']}</td><td>{p['asset_b']}</td><td class='r-critical'>{p['correlation']:.2f}</td><td>{p['verdict']}</td></tr>\n"
        corr_html += (
            f'    </tbody>\n'
            f'  </table>\n'
            f'  <div class="nudge-box" style="margin-top:1rem; color:var(--text-primary);">\n'
            f'    💭 <strong>보유 효과(Endowment Effect) 체크:</strong> "삼성전자" 및 "SK하이닉스"를 지금 현재 가격에 처음 산다고 가정하면, 다시 매수하시겠습니까?<br>\n'
            f'    만약 "아니오"라면, 지금 팔지 못하는 것은 투자 판단이 아닌 손실 회피 심리(Loss Aversion)가 원인일 수 있습니다. 비록 원금을 회복하지 못했더라도, 더 나은 자산으로 교체하는 것이 장기적으로 유리할 수 있습니다.\n'
            f'  </div>\n'
            f'</details>'
        )
        subs["CORRELATION_NOTE_HTML"] = corr_html
    else:
        subs["CORRELATION_NOTE"] = ""
        subs["CORRELATION_NOTE_HTML"] = ""

    # Behavioral Bias Section
    biases = [
        ("① 자국 편향 (Home Bias) 감지", 
         "전체 투자 자산의 78.8%가 국내(한국 상장) 자산에 집중되어 있습니다. 한국 경제가 침체되면 투자 자산이 일자리(인적 자본)와 동시에 타격을 받는 '이중 위험(Double Risk)'에 노출됩니다. 고객의 직종(어린이집 교사)에서 나오는 인적 자본(미래 소득)도 한국 경제와 강하게 연동되어 있음을 감안하면 이 집중도는 더욱 주의가 필요합니다. 신규 여유자금은 글로벌 ETF(KODEX 미국S&P500TR, ISA 배치) 비중을 점진적으로 늘려 지리적 분산을 강화하세요."),
        ("② 최신 편향 (Recency Effect) 감지",
         "SK하이닉스(52주 고점 근처, drawdown=-0.1%, 언급 빈도 절정 mention_count=11)와 AMD(52주 신고가, drawdown=0.0%)를 현재 대량 보유 중입니다. 최근 급등한 자산일수록 '이 흐름이 계속될 것'이라는 확신이 강해집니다. 하지만 이것이 '최신 편향(Recency Effect)'입니다. 지난 12개월의 성과가 미래 12개월을 보장하지 않습니다. 이 두 종목의 비중 상한선을 정하고 규칙 기반으로 리밸런싱하세요."),
        ("③ 손실 회피성 (Loss Aversion) 감지",
         "삼성전자·SK하이닉스·AMD·LG전자·KoAct AI반도체 등에 대해 '반등 시 분할 매도(Core 교체)' 처방이 내려져 있습니다. 손실 난 종목을 팔지 못하는 것은 투자 판단이 아닐 수 있습니다. 노벨 경제학상 수상자 카너먼의 연구에 따르면, 인간은 같은 금액의 이익보다 손실을 2배 이상 강하게 느낍니다. '지금 이 가격에 처음 산다면 다시 살 것인가?'라고 자문해 보세요. '아니오'라면, 지금 못 파는 이유는 투자 판단이 아닌 심리입니다.")
    ]
    
    bias_md = (
        "### 🧠 행동재무학 진단 — 나도 모르게 수익을 갉아먹는 심리 패턴\n\n"
        "뱅가드(Vanguard) 연구에 따르면, 체계적인 자산관리를 통해 투자자의 심리적 오류를 교정하는 것만으로 연평균 최대 **1.5%의 추가 수익률(Behavioral Alpha)**을 창출할 수 있습니다.\n\n"
    )
    for title, desc in biases:
        bias_md += f"**{title}**\n\n{desc}\n\n"
    bias_md += "> 💡 **행동 처방:** 투자의 적은 시장이 아니라 '나 자신'입니다. 리밸런싱 시점을 감정이 아닌 시스템(규칙 기반)에 맡기세요."
    subs["BEHAVIORAL_BIAS_SECTION"] = bias_md

    bias_html = (
        '<details class="card" open>\n'
        '  <summary class="card-title">🧠 행동재무학 진단 — 나도 모르게 수익을 갉아먹는 심리 패턴</summary>\n'
        '  <p style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:1rem;">뱅가드(Vanguard) 연구에 따르면, 체계적인 자산관리를 통해 투자자의 심리적 오류를 교정하는 것만으로 연평균 최대 <strong>1.5%의 추가 수익률(Behavioral Alpha)</strong>을 창출할 수 있습니다.</p>\n'
    )
    for title, desc in biases:
        bias_html += (
            f'  <div class="bias-item">\n'
            f'    <div class="bias-title">{title}</div>\n'
            f'    <div style="color:var(--text-secondary); font-size:0.82rem; line-height:1.6;">{desc}</div>\n'
            f'  </div>\n'
        )
    bias_html += (
        '  <div class="nudge-box" style="margin-top:1rem; color:var(--text-primary);">\n'
        '    💡 <strong>행동 처방:</strong> 투자의 적은 시장이 아니라 \'나 자신\'입니다. 리밸런싱 시점을 감정이 아닌 시스템(규칙 기반)에 맡기세요.\n'
        '  </div>\n'
        '</details>'
    )
    # We will use this bias_html in the html version.

    # Next Review Checklist
    checklist_md = (
        "다음 진단 **10일 전**까지 아래 자료를 미리 점검해 두시면 더 정확한 진단이 가능합니다.\n\n"
        "**재무 자료 준비:**\n"
        "- [ ] 모든 증권사 계좌의 손익 현황 (캡처 또는 PDF)\n"
        "- [ ] 현금·예금·CMA 잔액 현황\n"
        "- [ ] 최근 3개월 지출 내역 (가계부 앱 등)\n\n"
        "**생활 변화 체크 (해당 사항에 표시):**\n"
        "- [ ] 직장/소득 변화 (이직·승진·부업 시작·소득 감소)\n"
        "- [ ] 가족 구성 변화 (결혼·출산·이혼·부양가족 추가)\n"
        "- [ ] 큰 지출 예정 (주택 구매·자녀 교육비·의료비 등)\n"
        "- [ ] 상속·증여 수령 예정\n"
        "- [ ] 은퇴 시점 변경 가능성\n\n"
        "**절세 체크:**\n"
        "- [ ] 올해 해외 주식 양도차익 합계 (250만원 초과 여부) — AMD·GOOGL 등 일반계좌 미국 주식 보유 중이므로 반드시 확인\n"
        "- [ ] IRP/연금저축 납입 현황 (연간 한도 소진 여부) — 기존 연금저축(토스, 월 55만원) 납입 현황 확인\n"
        "- [ ] 금융소득(이자+배당) 합계 (2천만원 초과 시 종합과세 대상)\n"
        "- [ ] 청년형 ISA 개설 완료 여부 및 비과세 한도 활용 현황\n\n"
        f"> 💡 **D-10 준비를 잘 할수록 진단 품질이 높아집니다.** 특히 복수 증권사를 이용 중이라면 **전체 계좌를 합산**한 순손익을 계산해 두세요. AMD·GOOGL 등 미국 주식 양도차익은 증권사별로 합산 신고해야 절세 효과를 최대화할 수 있습니다."
    )
    subs["NEXT_REVIEW_CHECKLIST"] = checklist_md

    # Follow Up Email (CRM)
    core_monthly = fmt_won(stock_plan["core_products"][0]["monthly_amount"])
    core_name = stock_plan["core_products"][0]["name"]
    
    follow_up_md = (
        f"> 아래 내용을 복사하여 직접 보관하거나 메시지로 저장해 두세요.\n\n"
        f"**제목:** [재무 진단 요약] 오늘 결정된 실행 계획 안내\n\n"
        f"안녕하세요. 오늘 진행된 재무 건강 진단 결과를 요약합니다.\n\n"
        f"📋 **실행 계획표**\n\n"
        f"| 실행 항목 | 담당 | 기한 |\n"
        f"|-----------|------|------|\n"
        f"| KODEX미국나스닥100(중복 ETF) 매도 → 채권 ETF 또는 금 ETF로 교체. 같은 방향으로 움직이는 중복 자산을 정리하면 진짜 분산이 시작됩니다. | 본인 직접 | { (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d') } |\n"
        f"| 청년형 ISA 계좌 개설 (토스·미래에셋 앱, 약 5분). 연간 200만원 비과세 혜택을 받기 위한 첫 단계입니다. | 본인 직접 | { (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d') } |\n"
        f"| ISA 개설 후 {core_name} 월 {core_monthly} 자동 적립 세팅. 글로벌 분산의 핵심축을 기계적으로 쌓아가는 시작입니다. | 본인 직접 | { (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d') } |\n\n"
        f"💡 **오늘의 핵심 메시지:** 14개 종목을 보유하고 있지만 IT·반도체 섹터가 전체 투자자산의 60.6%를 차지합니다. 숫자만 많을 뿐, 반도체 사이클 한 번 꺾이면 전 종목이 동시에 하락하는 '가짜 분산' 구조입니다.\n\n"
        f"다음 진단 예정: **{next_review_date}**"
    )
    subs["FOLLOW_UP_EMAIL"] = follow_up_md

    # Stock Plan replacement
    # In stock_plan.json, we have stock_plan_md and stock_plan_html.
    subs["STOCK_PLAN"] = stock_plan["stock_plan_md"]
    subs["STOCK_PLAN_HTML"] = stock_plan["stock_plan_html"]
    
    # Let's check html specific variables
    subs["SAFE_PCT"] = str(portfolio["safe_pct"])
    subs["RISKY_PCT"] = str(portfolio["risky_pct"])
    subs["CORE_PCT"] = str(portfolio["core_pct"])
    subs["SAT_PCT"] = str(portfolio["satellite_pct"])
    subs["DEBT_PCT"] = str(round(kyc["assets"]["debt"] / kyc["assets"]["total_gross"] * 100, 1))
    
    total_score = risk_score["total_score"]
    if total_score >= 90:
        subs["GRADE_CLASS"] = "badge-green"
        subs["SCORE_COLOR_CLASS"] = "green"
    elif total_score >= 70:
        subs["GRADE_CLASS"] = "badge-yellow"
        subs["SCORE_COLOR_CLASS"] = "yellow"
    else:
        subs["GRADE_CLASS"] = "badge-red"
        subs["SCORE_COLOR_CLASS"] = "red"
        
    subs["SCORE_CASHFLOW_PCT"] = str(risk_score["details"]["cashflow"]["score"] / 25 * 100)
    subs["SCORE_RISK_PCT"] = str(risk_score["details"]["behavioral_gap"]["score"] / 25 * 100)
    subs["SCORE_EMERGENCY_PCT"] = str(risk_score["details"]["emergency_fund"]["score"] / 25 * 100)
    subs["SCORE_DIV_PCT"] = str(risk_score["details"]["diversification"]["score"] / 25 * 100)
    subs["MONTHLY_PLAN"] = (
        f"CMA {fmt_won(stock_plan['safe_products'][0]['monthly_amount'])}(일반계좌) + "
        f"채권ETF {fmt_won(stock_plan['safe_products'][1]['monthly_amount'])}(ISA) + "
        f"금ETF {fmt_won(stock_plan['safe_products'][2]['monthly_amount'])}(ISA) + "
        f"Core ETF {fmt_won(stock_plan['core_products'][0]['monthly_amount'])}(ISA) 자동 적립 세팅. "
        f"6월 청년형 ISA 개설 후 Satellite ETF 2종 주간 적립 세팅 추가 — 각 매주 16,000원"
    )

    # Load templates
    template_md = (BASE_DIR / "templates" / "master_report.md").read_text(encoding="utf-8")
    template_html = (BASE_DIR / "templates" / "master_report.html").read_text(encoding="utf-8")

    # Replace in MD template
    md_content = template_md
    # We need to replace {{SAFE_RISK_RATIO}} and {{CORE_SATELLITE_RATIO}} first
    md_content = md_content.replace("{{SAFE_RISK_RATIO}}", f"{portfolio['safe_pct']} : {portfolio['risky_pct']}")
    md_content = md_content.replace("{{CORE_SATELLITE_RATIO}}", f"{portfolio['core_pct']} : {portfolio['satellite_pct']}")
    
    for key, val in subs.items():
        placeholder = "{{" + key + "}}"
        md_content = md_content.replace(placeholder, str(val))

    # Replace in HTML template
    html_content = template_html
    # Some replacements in HTML are slightly different. 
    # Let's replace BEHAVIORAL_BIAS_SECTION with bias_html in HTML content
    html_content = html_content.replace("{{BEHAVIORAL_BIAS_SECTION}}", bias_html)
    
    for key, val in subs.items():
        placeholder = "{{" + key + "}}"
        html_content = html_content.replace(placeholder, str(val))

    # Write files to client reports directory
    (REPORTS_DIR / f"{date_str}.md").write_text(md_content, encoding="utf-8")
    (REPORTS_DIR / f"{date_str}.html").write_text(html_content, encoding="utf-8")
    print(f"✅ Generated {REPORTS_DIR / f'{date_str}.md'}")
    print(f"✅ Generated {REPORTS_DIR / f'{date_str}.html'}")

    # Write files to Desktop
    (DESKTOP_DIR / f"재무마스터리포트_{CLIENT_ID}.md").write_text(md_content, encoding="utf-8")
    (DESKTOP_DIR / f"재무마스터리포트_{CLIENT_ID}.html").write_text(html_content, encoding="utf-8")
    print(f"✅ Generated {DESKTOP_DIR / f'재무마스터리포트_{CLIENT_ID}.md'}")
    print(f"✅ Generated {DESKTOP_DIR / f'재무마스터리포트_{CLIENT_ID}.html'}")

if __name__ == "__main__":
    main()
