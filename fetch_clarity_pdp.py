"""Clarity 일별 수집 — append-only.

⚠️ Clarity API는 최근 3일치만 준다. 매일 받지 않으면 그 데이터는 영구 소실된다.
   그래서 이 스크립트는 **절대 덮어쓰지 않는다**(--force 제외).

⏰ 반드시 KST 00:20 에 돌려야 한다.
   API의 24시간 창은 "호출 시각 기준 지난 24시간"이다. 아침 7시에 돌리면 창이
   7시간 밀려 GA4의 달력일과 비교가 불가능해진다.

사용:
  python fetch_clarity_pdp.py                # 어제(방금 끝난 KST 하루) 수집
  python fetch_clarity_pdp.py --dump-raw     # URL 차원 원본값 확인 (첫 실행 시 필수)
  python fetch_clarity_pdp.py --repair --days 3   # 최근 3일 합산으로 공백 보충
"""
import argparse
import sys

import clarity_api
from pdp_common import kst_date, kst_now, load_json, parse_product_no, save_json

SNAP_PATH = "data/clarity_snapshots.json"

# 자동 호출 4건. 5~7은 재시도, 8~10은 사람이 수동 조회할 몫으로 남긴다.
AUTO_CALLS = [
    ("URL", ("URL",)),
    ("URL|Device", ("URL", "Device")),
    ("Device", ("Device",)),                      # 1·2번의 1000행 잘림 검증용 기준값
    ("Source|Medium|Device", ("Source", "Medium", "Device")),
]
RESERVE = 3          # 사람 몫


def _date_key(num_of_days):
    """numOfDays=1 이면 방금 끝난 하루 = 어제(KST 00:20 실행 기준)."""
    return kst_date(1) if num_of_days == 1 else f"{kst_date(num_of_days)}~{kst_date(1)}"


def fetch(num_of_days=1, force=False, dump_raw=False):
    snaps = load_json(SNAP_PATH)
    snaps.setdefault("snapshots", {})
    key = _date_key(num_of_days)
    entry = snaps["snapshots"].setdefault(key, {})
    entry.setdefault("calls", {})

    entry["fetched_at"] = kst_now().isoformat(timespec="seconds")
    entry["num_of_days"] = num_of_days
    entry["window_kst_note"] = (
        "호출 시각 기준 지난 %d일. KST 달력일과 정확히 같지 않다." % num_of_days)
    if num_of_days > 1:
        entry["aggregate_only"] = True

    done = 0
    for name, dims in AUTO_CALLS:
        if name in entry["calls"] and not force:
            print(f"  [{name}] 이미 수집됨 — 건너뜀 (append-only)")
            continue
        if clarity_api.budget_left() <= RESERVE:
            print(f"  [{name}] 예산 부족으로 중단 "
                  f"(사용 {clarity_api.budget_used()}/{clarity_api.DAILY_LIMIT})",
                  file=sys.stderr)
            break
        try:
            payload = clarity_api.live_insights(num_of_days, dims, reserve=RESERVE)
        except clarity_api.RateLimited as e:
            print(f"  [{name}] {e}", file=sys.stderr)
            break
        except clarity_api.ClarityError as e:
            print(f"  [{name}] 실패: {e}", file=sys.stderr)
            continue

        metrics, truncated = clarity_api.flatten(payload, dims)
        rows = sum(len(v) for v in metrics.values())
        entry["calls"][name] = {
            "dims": list(dims), "rows": rows, "truncated": truncated,
            "metrics": metrics,
        }
        done += 1
        flag = "  ⚠ 1000행 근접(잘림 의심)" if truncated else ""
        print(f"  [{name}] {rows}행{flag}")

        if dump_raw and "URL" in dims:
            _dump_raw_urls(metrics)

    entry["call_count"] = len(entry["calls"])
    snaps["snapshots"][key] = entry
    save_json(SNAP_PATH, snaps)
    print(f"저장: {SNAP_PATH} · {key} · 이번 실행 {done}건 "
          f"(오늘 예산 {clarity_api.budget_used()}/{clarity_api.DAILY_LIMIT})")
    return entry


def _dump_raw_urls(metrics, limit=30):
    """URL 차원 원본값 확인 — 쿼리스트링이 유지되는지가 최대 리스크다.

    제거된다면 /product/detail.html?product_no= 형태 2종이 한 줄로 뭉개져
    상품 귀속이 불가능해진다. 그 경우 슬러그형만 상품별로 쓰고 나머지는
    _unknown 으로 분리 보고한다 (GA4 비율로 안분하지 않는다 — 추정이지 측정이 아니다).
    """
    seen = []
    for rows in metrics.values():
        for r in rows:
            u = r.get("URL") or r.get("Url") or r.get("url")
            if u and u not in seen:
                seen.append(u)
        if len(seen) >= limit:
            break
    print("\n--- URL 차원 원본값 (쿼리스트링 유지 여부 확인) ---")
    for u in seen[:limit]:
        print(f"  {parse_product_no(u) or '-':>4} | {u}")
    print("--- 위 왼쪽 열이 전부 '-' 면 파싱 규칙 점검 필요 ---\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--repair", action="store_true",
                    help="공백 보충용. 결과는 일별이 아니라 합산값이다.")
    ap.add_argument("--force", action="store_true", help="이미 있는 것도 덮어쓴다")
    ap.add_argument("--dump-raw", action="store_true")
    a = ap.parse_args()
    fetch(num_of_days=(a.days if not a.repair else max(a.days, 3)),
          force=a.force, dump_raw=a.dump_raw)
