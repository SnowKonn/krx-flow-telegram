# KRX 수급 리포트 → 텔레그램 자동 발송

매 영업일 장 마감 후 KRX 투자자별 수급에서 아래 3가지를 뽑아 텔레그램으로 보내고 CSV를 `data/`에 쌓는다.

| # | 리포트 | 기준 |
|---|---|---|
| ① | 기타법인·사모펀드 동시 순매수 상위 10 | 최근 5영업일 누적이 두 주체 모두 1억 이상 순매수, 합계 순 |
| ② | 연기금 순매도 상위 10 | 최근 20영업일 누적 순매수대금 하위 |
| ③ | 금융투자 3주 연속 순매수 상위 10 | 기준일부터 5영업일씩 자른 3개 주 모두 순매수, 3주 합계 순 |

같은 `collect.py` 하나를 **1단계 로컬 스케줄** → **2단계 클라우드(GitHub Actions, 무료)** 로 옮겨 가며 돌린다.

## 준비물 (3개)

| 이름 | 어디서 |
|---|---|
| `KRX_ID`, `KRX_PW` | [KRX 정보데이터시스템](https://data.krx.co.kr) 회원가입 계정 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 **@BotFather** → `/newbot` → 발급된 토큰 |

봇을 만든 뒤 텔레그램에서 그 봇에게 **`/start`** 를 한 번 보내 둔다. chat_id 는 스크립트가 이 메시지에서 자동으로 찾는다.

---

## 1단계 — 로컬에서 자동 실행

```bash
pip install -r requirements.txt
```

`collect.py` 와 같은 폴더에 `.env` 파일을 만든다 (git 에 올리지 않는다).

```
KRX_ID=내아이디
KRX_PW=내비밀번호
TELEGRAM_BOT_TOKEN=123456:ABC...
```

한 번 돌려 본다.

```bash
DRY_RUN=1 python collect.py   # 발송 없이 메시지만 출력 (Windows PowerShell: $env:DRY_RUN=1; python collect.py)
python collect.py             # 텔레그램 발송
python collect.py 20261016    # 특정일 기준
```

자동 실행은 셋 중 하나를 고른다.

**A. Python `schedule` (가장 쉬움, 창을 켜 둬야 함)**
```bash
python run_local.py --now     # 지금 1회 + 이후 평일 18:30 마다
```

**B. Windows 작업 스케줄러 (PC 만 켜져 있으면 됨)**
```powershell
schtasks /Create /TN "KRX수급" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:30 `
  /TR "\"C:\경로\python.exe\" \"C:\경로\krx-flow-telegram\collect.py\""
schtasks /Run /TN "KRX수급"      # 즉시 테스트
```

**C. macOS / Linux cron**
```bash
crontab -e
30 18 * * 1-5 /경로/python /경로/krx-flow-telegram/collect.py >> /경로/krx.log 2>&1
```

> 로컬의 한계: PC 가 꺼져 있거나 절전 중이면 돌지 않는다 → 2단계.

---

## 2단계 — 클라우드(GitHub Actions)에서 무료로

1. 이 폴더를 GitHub repo 로 올린다 (**Use this template** / Fork / 새 repo 에 push).
2. `Settings → Secrets and variables → Actions → New repository secret` 에 `KRX_ID`, `KRX_PW`, `TELEGRAM_BOT_TOKEN` 등록.
3. `Actions` 탭 → (Fork 라면 활성화 버튼 클릭) → **KRX 수급 → 텔레그램** → **Run workflow**.
4. 1~2분 뒤 텔레그램 도착. 이후 평일 18:30(KST)마다 자동 실행되고, CSV 는 repo 의 `data/` 에 커밋된다.

- 비용: private repo 월 2,000분 무료, 1회 실행 약 1분 → 사실상 0원.
- GitHub 의 cron 은 수 분~수십 분 늦게 돌 수 있다. 시연은 **Run workflow** 수동 실행으로 한다.
- chat_id 자동 탐색은 봇이 최근 24시간 안에 받은 메시지에 의존한다. 매일 자동 실행을 쓰려면 첫 실행 로그에 찍힌
  chat_id 를 `TELEGRAM_CHAT_ID` Secret 으로 등록한다.
- 선택: `HF_TOKEN`(Secret) + `HF_REPO`(Variable, 예: `myname/krx-flows`) 를 넣으면 Hugging Face Dataset 에도 CSV 를 올린다.

## 참고

- 수급 수치는 시간외 거래 종료(18:00) 전까지 계속 바뀐다. 그래서 18:30 에 실행한다.
- ③ 의 '주'는 달력 주가 아니라 기준일부터 거꾸로 자른 5영업일 블록이다 (주 중간에도 완결된 3주를 비교).
- 기간·기준값은 `collect.py` 상단 상수(`CUM_DAYS`, `PENSION_DAYS`, `N_WEEKS` 등)로 바꾼다.
- 기타법인 순매수에는 회사의 자사주 매입 등 법인 계정 매매가 섞인다. 개별 사유는 공시로 확인한다.
