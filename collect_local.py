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
        print("!! 카페24 refresh 토큰이 만료됐습니다. 재인증이 필요합니다:\n"
              "   cd C:\\Users\\dongs\\vegicel-ad-autopilot\n"
              "   python cafe24_api.py --auth-url", file=sys.stderr)
        raise SystemExit(2)
    if left < WARN_DAYS_LEFT:
        print("! 카페24 refresh 토큰 잔여 %.1f일. 곧 재인증이 필요합니다." % left,
              file=sys.stderr)
    else:
        print("카페24 토큰 잔여 %.1f일" % left)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("=== %s 로컬 수집 시작 ===" % C.kst_now().strftime("%Y-%m-%d %H:%M"))

    check_token()

    # 봇이 원격에 커밋해 두므로 먼저 받아야 push 가 거부되지 않는다.
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    py = sys.executable
    run([py, "fetch_cafe24_pdp.py", "--days", str(args.days)])
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
