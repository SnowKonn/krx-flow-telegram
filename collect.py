"""KRX 투자자별 수급 리포트 3종 → CSV 저장 → 텔레그램 발송 (세미나 데모).

  ① 기타법인·사모펀드 동시 순매수 (5영업일 누적이 둘 다 +)
  ② 연기금 순매도 상위 (20영업일 누적)
  ③ 금융투자 3주 연속 순매수 (5영업일 단위 주 3개 모두 +)

필요한 환경변수 (로컬은 .env, GitHub 은 Secrets):
  KRX_ID, KRX_PW        KRX 정보데이터시스템(data.krx.co.kr) 계정 — pykrx 가 import 시 자동 로그인
  TELEGRAM_BOT_TOKEN    @BotFather 에서 발급한 봇 토큰
선택:
  TELEGRAM_CHAT_ID      비우면 봇에게 최근 보낸 메시지(getUpdates)에서 자동 탐색
  HF_TOKEN, HF_REPO     있으면 Hugging Face Dataset 에 CSV 업로드
  DRY_RUN=1             텔레그램 발송 없이 메시지만 출력

사용: python collect.py [YYYYMMDD]   (날짜 생략 시 가장 최근 영업일)
"""
from __future__ import annotations

import datetime as dt
import html
import os
import sys
from pathlib import Path

try:  # 로컬 실행: .env 에서 계정 읽기 (GitHub Actions 에서는 Secrets 가 환경변수로 들어옴)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

import pandas as pd
import requests
from pykrx import stock  # KRX_ID/KRX_PW 가 있으면 여기서 로그인

EOK = 1e8              # 원 → 억원
TOP_N = 10
CUM_DAYS = 5           # ① 누적 기간(영업일)
JOINT_MIN_EOK = 1      # ① 주체별 최소 순매수(억원) — 사실상 0 인 종목 제외
PENSION_DAYS = 20      # ② 누적 기간(영업일)
WEEK_DAYS = 5          # ③ 1주 = 5영업일
N_WEEKS = 3            # ③ 연속 주 수
DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------- 수집
def business_days(end: str, n: int) -> list[str]:
    """end 포함 최근 n 영업일 (KOSPI 지수 거래일 기준)."""
    start = (dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=n * 2 + 14)).strftime("%Y%m%d")
    days = [d.strftime("%Y%m%d") for d in stock.get_index_ohlcv(start, end, "1001").index]
    return days[-n:]


def net_buy(start: str, end: str, investor: str) -> pd.DataFrame:
    """전체 시장 종목별 순매수(억원). 기간이면 누적."""
    df = stock.get_market_net_purchases_of_equities(start, end, "ALL", investor)
    return pd.DataFrame({"종목명": df["종목명"], "순매수(억)": df["순매수거래대금"] / EOK})


def report_joint_buy(days: list[str]) -> pd.DataFrame:
    """① 기타법인·사모 5일 누적이 둘 다 순매수인 종목, 합산 순."""
    start, end = days[-CUM_DAYS], days[-1]
    corp, pef = net_buy(start, end, "기타법인"), net_buy(start, end, "사모")
    df = corp.join(pef["순매수(억)"], how="inner", lsuffix="_기타법인", rsuffix="_사모")
    df.columns = ["종목명", "기타법인(억)", "사모(억)"]
    df = df[(df["기타법인(억)"] >= JOINT_MIN_EOK) & (df["사모(억)"] >= JOINT_MIN_EOK)]
    df["합계(억)"] = df["기타법인(억)"] + df["사모(억)"]
    return df.nlargest(TOP_N, "합계(억)")


def report_pension_sell(days: list[str]) -> pd.DataFrame:
    """② 연기금 20일 누적 순매도 상위."""
    return net_buy(days[-PENSION_DAYS], days[-1], "연기금").nsmallest(TOP_N, "순매수(억)")


def report_fi_weekly(days: list[str]) -> pd.DataFrame:
    """③ 금융투자: 기준일부터 거꾸로 자른 5영업일 블록 3개에서 모두 순매수, 3주 합산 순."""
    weeks = {}
    for w in range(N_WEEKS, 0, -1):  # 오래된 주 → 최근 주
        blk = days[len(days) - w * WEEK_DAYS: len(days) - (w - 1) * WEEK_DAYS]
        weeks[f"{N_WEEKS - w + 1}주(억)"] = net_buy(blk[0], blk[-1], "금융투자")
    names = pd.concat([df["종목명"] for df in weeks.values()]).groupby(level=0).last()
    amt = pd.DataFrame({k: df["순매수(억)"] for k, df in weeks.items()}).dropna()
    amt = amt[(amt > 0).all(axis=1)]
    out = amt.assign(**{"합계(억)": amt.sum(axis=1)}).join(names)
    return out[["종목명", *weeks, "합계(억)"]].nlargest(TOP_N, "합계(억)")


# ---------------------------------------------------------------- 저장
def save_csv(d: str, tables: dict[str, pd.DataFrame]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for name, df in tables.items():
        df.round(1).reset_index(names="티커").to_csv(DATA_DIR / f"{d}_{name}.csv", index=False, encoding="utf-8-sig")


def upload_hf(d: str) -> None:
    token, repo = os.getenv("HF_TOKEN"), os.getenv("HF_REPO")
    if not (token and repo):
        return
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", exist_ok=True, private=True)
    for f in DATA_DIR.glob(f"{d}_*.csv"):
        api.upload_file(path_or_fileobj=f, path_in_repo=f"data/{f.name}", repo_id=repo, repo_type="dataset")
    print(f"[hf] uploaded to {repo}")


# ---------------------------------------------------------------- 메시지
def fmt_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def rank_lines(df: pd.DataFrame, col: str, detail=None) -> list[str]:
    lines = []
    for i, (_, r) in enumerate(df.iterrows(), 1):
        tail = f"  ({detail(r)})" if detail else ""
        lines.append(f"{i}. {html.escape(r['종목명'])}  {r[col]:+,.0f}억{tail}")
    return lines or ["(해당 종목 없음)"]


def build_messages(days: list[str], joint, pension, fi) -> list[str]:
    d = fmt_date(days[-1])
    m1 = [f"<b>① 기타법인·사모 동시 순매수</b>  {fmt_date(days[-CUM_DAYS])} ~ {d}",
          f"({CUM_DAYS}일 누적 둘 다 {JOINT_MIN_EOK}억 이상 순매수, 합계 순 · 기타법인/사모)", ""]
    m1 += rank_lines(joint, "합계(억)", lambda r: f"{r['기타법인(억)']:+,.0f} / {r['사모(억)']:+,.0f}")
    m2 = [f"<b>② 연기금 순매도 상위 ({PENSION_DAYS}일 누적)</b>",
          f"{fmt_date(days[-PENSION_DAYS])} ~ {d}", ""] + rank_lines(pension, "순매수(억)")
    wk = [c for c in fi.columns if c.endswith("주(억)")]
    m3 = [f"<b>③ 금융투자 {N_WEEKS}주 연속 순매수</b>  {fmt_date(days[-N_WEEKS * WEEK_DAYS])} ~ {d}",
          f"({WEEK_DAYS}영업일 단위 주 {N_WEEKS}개 모두 순매수, 합계 순 · 주별)", ""]
    m3 += rank_lines(fi, "합계(억)", lambda r: " → ".join(f"{r[c]:+,.0f}" for c in wk))
    return ["\n".join(m) for m in (m1, m2, m3)]


# ---------------------------------------------------------------- 텔레그램
def resolve_chat_id(token: str) -> str:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        return chat_id
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    chats = [u[k]["chat"]["id"] for u in r.get("result", [])
             for k in ("message", "channel_post", "my_chat_member") if k in u]
    if not chats:
        sys.exit("[telegram] chat_id 를 찾지 못했습니다. 봇에게 /start 를 보낸 뒤 다시 실행하세요.")
    print(f"[telegram] chat_id 자동 탐색: {chats[-1]} (매일 자동 실행하려면 TELEGRAM_CHAT_ID 로 등록 권장)")
    return str(chats[-1])


def send_telegram(texts: list[str]) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = resolve_chat_id(token)
    for text in texts:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML"}, timeout=30)
        if r.status_code != 200:
            sys.exit(f"[telegram] 발송 실패 {r.status_code}: {r.text}")
    print(f"[telegram] {len(texts)}건 발송")


# ---------------------------------------------------------------- main
def main() -> None:
    end = sys.argv[1] if len(sys.argv) > 1 else stock.get_nearest_business_day_in_a_week(
        dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d"))
    days = business_days(end, max(PENSION_DAYS, N_WEEKS * WEEK_DAYS))
    if not days or days[-1] != end:
        sys.exit(f"[krx] {end} 거래 데이터가 없습니다 (휴장일이거나 로그인 실패).")
    print(f"[krx] 기준일 {end}")

    joint = report_joint_buy(days)
    pension = report_pension_sell(days)
    fi = report_fi_weekly(days)

    save_csv(end, {"기타법인사모_동시순매수": joint, "연기금_20일순매도": pension, "금융투자_3주연속": fi})
    upload_hf(end)

    msgs = build_messages(days, joint, pension, fi)
    if os.getenv("DRY_RUN"):
        print("\n\n".join(msgs))
    else:
        send_telegram(msgs)


if __name__ == "__main__":
    main()
