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
import clarity_slim
from datetime import datetime, timedelta

from pdp_common import kst_now, load_json, parse_product_no, save_json

SNAP_PATH = "data/clarity_snapshots.json"   # 원본. 용량 때문에 gitignore — 로컬 전용
SLIM_PATH = "data/clarity_slim.json"        # 저장소에 올라가는 정본

# 자동 호출 4건. 5~7은 재시도, 8~10은 사람이 수동 조회할 몫으로 남긴다.
# 2026-08-17: 4콜 -> 2콜로 줄임.
#
# 병합·화면이 실제로 읽는 건 URL 하나뿐인데(merge_pdp.py 의 calls["URL"]) 나머지 3콜은
# 예산만 태우고 있었다. 실제로 8/15~8/16 에 "Exceeded daily limit" 이 나서 그날 수집이
# 통째로 실패했다 — 안 쓰는 데이터 때문에 쓰는 데이터를 잃은 셈이다.
#
# Source|Medium 은 유일하게 유튜브 유입을 잡아주는 값이라(GA4 는 미분류로 뭉갬) 남긴다.
# URL|Device 와 Device 는 필요해지면 그때 되살린다.
AUTO_CALLS = [
    ("URL", ("URL",)),
    ("Source|Medium|Device", ("Source", "Medium", "Device")),
]
RESERVE = 3          # 사람 몫


def _window(num_of_days):
    """API 창은 '호출 시각 기준 지난 N일'이라 달력일이 아니다.

    00:20에 돌리면 창이 어제 하루와 거의 일치하지만, 다른 시각에 돌리면 어긋난다.
    창의 중간 지점이 속한 날짜를 키로 삼고, 실제 경계와 정렬 여부를 함께 남긴다.
    라벨을 실제와 다르게 붙이면 나중에 GA4와 대조할 때 조용히 틀린다.
    """
    end = kst_now()
    start = end - timedelta(days=num_of_days)
    mid = start + (end - start) / 2
    key = mid.strftime("%Y-%m-%d")
    # 00:00~01:00 사이 실행이면 창이 직전 달력일과 거의 일치한다
    aligned = end.hour == 0 or (end.hour == 1 and end.minute < 30)
    return key, {
        "window_start_kst": start.isoformat(timespec="seconds"),
        "window_end_kst": end.isoformat(timespec="seconds"),
        "aligned_to_calendar_day": aligned,
    }


def fetch(num_of_days=1, force=False, dump_raw=False):
    snaps = load_json(SNAP_PATH)
    snaps.setdefault("snapshots", {})
    key, win = _window(num_of_days)
    date_key = key

    # 같은 날짜 키에 **다른 창**이 이미 있으면 덮지도 건너뛰지도 않고 새 키로 넣는다.
    #
    # 창이 '호출 시각 기준 지난 24시간'이라 실행 시각이 밀리면 중간값 날짜가 기존
    # 스냅샷과 겹친다. 예약 실행이 실패한 뒤 낮에 복구하려 하면 키가 충돌해
    # "이미 수집됨"으로 건너뛰고, 그 데이터는 3일 뒤 영구 소실된다.
    # (실제로 2026-08-07 02:02 예약 실행이 GitHub 러너 배정 실패로 죽었다.)
    prev = snaps["snapshots"].get(key)
    if prev and not force:
        prev_start = (prev.get("window_start_kst") or "")[:16]
        if prev_start and prev_start != win["window_start_kst"][:16]:
            i = 2
            while "%s#%d" % (date_key, i) in snaps["snapshots"]:
                i += 1
            key = "%s#%d" % (date_key, i)
            print("  같은 날짜에 다른 창이 이미 있어 새 키로 저장합니다: %s" % key)

    entry = snaps["snapshots"].setdefault(key, {})
    entry.setdefault("calls", {})
    entry["date_key"] = date_key

    entry["fetched_at"] = kst_now().isoformat(timespec="seconds")
    entry["num_of_days"] = num_of_days
    entry.update(win)
    entry["window_kst_note"] = (
        "호출 시각 기준 지난 %d일. KST 달력일과 정확히 같지 않다." % num_of_days)
    if not win["aligned_to_calendar_day"]:
        print(f"  ⚠ 실행 시각이 00:20이 아니라 창이 달력일과 어긋납니다 "
              f"({win['window_start_kst'][:16]} ~ {win['window_end_kst'][:16]})")
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

    # 요약본은 통째로 다시 만들지 않고 이 한 건만 얹는다.
    # Actions 러너에는 원본이 없어서(gitignore) 통째로 만들면 과거가 전부 날아간다.
    slim = load_json(SLIM_PATH)
    slim.setdefault("snapshots", {})[key] = clarity_slim.slim_entry(entry)
    save_json(SLIM_PATH, slim)
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
    ap.add_argument("--only-if-stale-hours", type=float, default=0,
                    help="마지막 수집이 이보다 최근이면 호출하지 않고 종료. "
                         "예약 실행 실패에 대비한 재시도용.")
    a = ap.parse_args()

    # 재시도 스케줄이 정상 수집분 위에 또 호출하면 하루 10회 예산을 태운다.
    # 마지막 수집 시각을 보고 이미 최신이면 아무것도 하지 않는다.
    if a.only_if_stale_hours > 0:
        snaps = (load_json(SLIM_PATH).get("snapshots") or {})
        if not snaps:
            snaps = (load_json(SNAP_PATH).get("snapshots") or {})
        last = max((v.get("fetched_at") or "" for v in snaps.values()),
                   default="")
        if last:
            try:
                age = (kst_now() - datetime.fromisoformat(last)).total_seconds() / 3600
                if age < a.only_if_stale_hours:
                    print("마지막 수집이 %.1f시간 전 — 아직 최신이라 건너뜁니다." % age)
                    raise SystemExit(0)
                print("마지막 수집이 %.1f시간 전 — 수집을 진행합니다." % age)
            except ValueError:
                pass

    fetch(num_of_days=(a.days if not a.repair else max(a.days, 3)),
          force=a.force, dump_raw=a.dump_raw)
