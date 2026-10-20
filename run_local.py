"""로컬 스케줄러 (세미나 1단계): 켜둔 PC 에서 평일 18:30 마다 collect.py 실행.

    python run_local.py          # 스케줄 대기 (Ctrl+C 로 종료)
    python run_local.py --now    # 지금 한 번 실행 후 스케줄 대기

PC 가 꺼져 있으면 돌지 않는다 → OS 스케줄러(cron / 작업 스케줄러) 또는 2단계 GitHub Actions 로.
"""
import subprocess
import sys
import time
from pathlib import Path

import schedule

COLLECT = Path(__file__).with_name("collect.py")


def job() -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] collect.py 실행"), flush=True)
    subprocess.run([sys.executable, str(COLLECT)], check=False)


for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), day).at("18:30").do(job)

if "--now" in sys.argv:
    job()
print("스케줄 대기 중… (평일 18:30)", flush=True)
while True:
    schedule.run_pending()
    time.sleep(30)
