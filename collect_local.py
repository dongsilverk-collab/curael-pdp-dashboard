"""로컬 전용 수집 파이프라인 — 카페24 → 병합 → 화면 → push.

카페24만 로컬인 이유는 refresh 토큰이 쓸 때마다 교체돼서 Actions 와 경합하면
깨지기 때문이다(cafe24_pdp_api.py 상단 참고). GA4·Clarity 는 Actions 가 맡는다.

로직을 배치(.bat)가 아니라 여기 두는 이유: 배치에 조건문·에러처리를 넣으면
실패했을 때 어디서 멈췄는지 알 수 없다. 배치는 이 파일을 부르기만 한다.

사용:
  python collect_local.py              # 어제~오늘 수집 후 push
  python collect_local.py --days 30    # 백필
  python collect_local.py --no-push    # 커밋까지만 (시험용)
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pdp_common as C

# refresh 토큰 만료가 이보다 가까우면 경고한다. 만료되면 브라우저 재인증이 필요한데,
# 깨지고 나서 알면 그날 수집은 이미 놓친 뒤다.
WARN_DAYS_LEFT = 4


def run(cmd, check=True):
    print("\n$ %s" % " ".join(cmd))
    r = subprocess.run(cmd, shell=False)
    if check and r.returncode != 0:
        raise SystemExit("실패: %s (exit %d)" % (" ".join(cmd), r.returncode))
    return r.returncode


def check_token():
    """카페24 refresh 토큰 잔여일 확인. 수집 전에 본다 — 만료면 뭘 해도 실패한다."""
    import cafe24_pdp_api as API
    try:
        p = API.token_path()
        with open(p, encoding="utf-8") as f:
            tok = json.load(f)
    except Exception as e:
        print("! 토큰 파일을 읽을 수 없습니다: %s" % e, file=sys.stderr)
        return
    raw = (tok.get("refresh_token_expires_at") or "").replace("Z", "")
    try:
        exp = datetime.fromisoformat(raw)
    except ValueError:
        return
    left = (exp - datetime.now(exp.tzinfo) if exp.tzinfo
            else exp - datetime.now()).total_seconds() / 86400
    if left < 0:
        # vegicel 쪽 스크립트를 안내하면 안 된다 — 자격증명을 환경변수에서만 읽어서
        # 그냥 연 PowerShell 에서는 "CAFE24_MALL_ID 환경변수 필요"로 막힌다.
        # 이 저장소 것은 .env 를 읽으므로 그대로 붙여넣으면 된다.
        print("!! 카페24 refresh 토큰이 만료됐습니다. 재인증이 필요합니다:\n"
              "   cd C:\\Users\\dongs\\curael-pdp-dashboard\n"
              "   python cafe24_pdp_api.py --auth-url", file=sys.stderr)
        raise SystemExit(2)
    if left < WARN_DAYS_LEFT:
        print("! 카페24 refresh 토큰 잔여 %.1f일. 곧 재인증이 필요합니다." % left,
              file=sys.stderr)
    else:
        print("카페24 토큰 잔여 %.1f일" % left)


MAX_BACKFILL = 60   # PC를 오래 꺼뒀어도 이 이상은 한 번에 안 받는다(호출 폭주 방지)


def days_to_fetch(minimum=2):
    """마지막 수집일 이후의 공백을 스스로 메운다.

    이 배치는 PC가 켜져 있을 때만 돈다. 며칠 쉬면 그 사이가 비는데, 고정 --days 2 면
    영영 안 채워진다. 카페24 주문 API 는 Clarity 와 달리 **과거를 언제든 다시 주므로**
    공백은 전부 복구 가능하다 — 안 받는 게 손해다.
    """
    hist = C.load_json("data/cafe24_pdp_history.json", {}).get("days") or {}
    if not hist:
        return minimum
    last = max(hist)
    try:
        gap = (datetime.strptime(C.kst_today(), "%Y-%m-%d")
               - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return minimum
    # 오늘은 아직 주문이 들어오는 중이라 어제치도 다시 받아야 확정된다.
    return max(minimum, min(gap + 1, MAX_BACKFILL))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0,
                    help="0이면 마지막 수집일 기준으로 자동 판단")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("=== %s 로컬 수집 시작 ===" % C.kst_now().strftime("%Y-%m-%d %H:%M"))

    check_token()

    # 봇이 원격에 커밋해 두므로 먼저 받아야 push 가 거부되지 않는다.
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    py = sys.executable
    n = args.days or days_to_fetch()
    if n > 2:
        print("\n마지막 수집 이후 공백이 있어 최근 %d일을 받습니다." % n)
    run([py, "fetch_cafe24_pdp.py", "--days", str(n)])
    run([py, "merge_pdp.py"])
    run([py, "build_pdp_dashboard.py"])

    subprocess.run(["git", "add", "data", "docs"], shell=False)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], shell=False)
    if staged.returncode == 0:
        print("\n변경 없음 — 커밋 생략")
        return 0

    run(["git", "commit", "-m",
         "Cafe24 sales %s" % C.kst_now().strftime("%Y-%m-%d")])
    if args.no_push:
        print("\n--no-push — 커밋까지만 했습니다")
        return 0
    run(["git", "push"])
    print("\n완료. Vercel 이 자동으로 재배포합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
