"""data/*.json 을 상품번호로 묶어 data/pdp_daily.json 하나로 만든다.

**이 스크립트는 네트워크를 호출하지 않는다.** 수집기들은 서로 다른 시각·주체로 돌고
(GA4/버전은 Actions, 카페24는 로컬) 실패 모드가 제각각이다. 병합이 수집을 겸하면
하나가 죽었을 때 나머지로도 화면을 못 만든다. 여기서는 파일만 읽는다 —
몇 번을 다시 돌려도 같은 결과가 나와야 한다.

화면이 계산 없이 바로 그릴 수 있는 형태까지 만들어 둔다. 평균·비율을 대시보드가
계산하게 하면 같은 로직이 두 군데 생기고 반드시 어긋난다.
"""
import sys

import pdp_common as C

GA4 = "data/ga4_pdp_history.json"
CLARITY = "data/clarity_snapshots.json"
VERSIONS = "data/pdp_versions.json"
PRODUCTS = "data/pdp_products.json"
CAFE24 = "data/cafe24_pdp_history.json"   # 아직 수집기 없음. 없으면 미수집으로 표시.
OUT = "data/pdp_daily.json"

MIN_SESSIONS = 30      # 이 미만은 순위에서 빼고 '표본 부족'으로 따로 보여준다


def _blank():
    return {"section_reach": {}, "exit_hist": {}, "events": {}, "exit_events": 0}


def _add(dst, src):
    """기기별 버킷을 합쳐 _all 을 만든다. 합산 로직이 화면에 또 생기지 않도록 여기서 끝낸다."""
    dst["exit_events"] += src.get("exit_events", 0)
    for key in ("section_reach", "exit_hist", "events"):
        for k, v in (src.get(key) or {}).items():
            dst[key][k] = dst[key].get(k, 0) + v
    for k, v in src.items():
        if k.startswith("sum_"):
            dst[k] = dst.get(k, 0) + v
    return dst


def _derive(bucket, total_sections):
    """도달률·낙차·평균을 확정한다.

    분모는 pdp_exit 건수다. 이 이벤트는 세션당 1회 발사되므로 '상세페이지를 연 세션 수'에
    가장 가깝다. pdp_section 은 구간마다 발사되므로 분모로 쓰면 안 된다.
    """
    sessions = bucket.get("exit_events", 0)
    reach = {int(k): v for k, v in (bucket.get("section_reach") or {}).items()}
    out = dict(bucket)
    out["sessions"] = sessions

    # 도달 수를 뒤에서부터 누적 최대로 보정한다.
    #
    # 추적 스크립트는 이미지 높이가 1px 이하면(레이지 로드 미완) 안 본 것으로 처리한다.
    # 그런데 그 이미지가 자리를 차지하지 않으니 뒤 이미지들이 위로 당겨져 같은 스크롤
    # 깊이에서 더 많이 잡힌다. 그래서 뒤 구간 도달이 앞 구간보다 커지는 일이 생긴다
    # (26번 실측: 20구간 중 6군데). S18 을 봤다면 S14 자리는 반드시 지나갔으므로,
    # 뒤 구간 값이 크면 앞 구간도 최소 그만큼은 도달한 것이다.
    #
    # 원본을 지우지 않고 section_reach 로 남겨둔다 — 보정은 추론이고 원본은 측정이다.
    mono, run = {}, 0
    for i in range(total_sections, 0, -1):
        run = max(run, reach.get(i, 0))
        mono[i] = run
    repaired = sum(1 for i in range(1, total_sections + 1)
                   if mono.get(i, 0) != reach.get(i, 0))
    out["section_reach_mono"] = {str(i): mono[i] for i in mono}
    out["reach_repaired_sections"] = repaired

    pct, drop = {}, {}
    if sessions and total_sections:
        prev = None
        for i in range(1, total_sections + 1):
            p = mono.get(i, 0) / sessions
            pct[str(i)] = round(p, 4)
            if prev is not None:
                drop[str(i)] = round(prev - p, 4)
            prev = p
    out["section_reach_pct"] = pct
    out["section_dropoff"] = drop

    # 도달률이 뒤 구간에서 되레 오르면 측정 잡음이다(스크롤은 아래로만 간다).
    # 레이지 로드가 늦거나 빠르게 지나치면 그 구간 기록이 빠진다.
    # 잡음이 있으면 도달률 차이로 낸 낙차를 믿을 수 없다는 표시를 남긴다.
    bumps = sum(1 for k, v in drop.items() if v < -0.02)
    out["reach_noise"] = bumps

    # 최대 낙차는 pdp_exit 분포에서 고른다.
    #
    # 처음엔 도달률 차이로 계산했는데 그건 간접 신호다. 실제로 26번 S05 는 도달률이
    # 5명 줄어 '최대 낙차'로 뽑혔지만 그 구간에서 세션이 끝난 사람은 0명이었고,
    # 바로 뒤 S06·S07 에서 도달률이 다시 올라갔다 — 이탈이 아니라 누락이었다.
    # pdp_exit 은 세션이 어디서 끝났는지를 직접 센다. 질문이 "어디서 나갔나"라면
    # 직접 센 값을 써야 한다.
    ex_all = {int(k): v for k, v in (bucket.get("exit_hist") or {}).items()
              if str(k).isdigit()}
    # S01 은 제외한다. 3초 미만 이탈(유입 품질·광고 소재 불일치)이 여기 몰리는데
    # 그건 이미지 문제가 아니라 처방이 완전히 다르다. S00(미도달)도 당연히 제외.
    cand = {i: n for i, n in ex_all.items()
            if i >= 2 and (not total_sections or i < total_sections)}
    if cand and sessions:
        i = max(cand, key=lambda k: cand[k])
        out["biggest_drop_section"] = i
        out["biggest_drop_people"] = cand[i]
        out["biggest_drop_rate"] = round(cand[i] / sessions, 4)
    else:
        out["biggest_drop_section"] = None
        out["biggest_drop_rate"] = 0.0
        out["biggest_drop_people"] = 0

    # 맞춤 측정항목은 GA4가 합계로 준다. 평균은 여기서 한 번만 만든다.
    for name in ("seconds", "bounce_3s", "saw_cta", "saw_review",
                 "clicked_cart", "clicked_buy"):
        s = bucket.get("sum_" + name)
        if s is not None and sessions:
            out["avg_" + name] = round(s / sessions, 4)

    ex = {int(k): v for k, v in (bucket.get("exit_hist") or {}).items()
          if str(k).isdigit()}
    out["exit_s00"] = ex.get(0, 0)
    out["exit_s01"] = ex.get(1, 0)
    out["completed"] = ex.get(total_sections, 0) if total_sections else 0
    for k, label in (("exit_s00", "s00"), ("exit_s01", "s01"), ("completed", "done")):
        out[label + "_rate"] = round(out[k] / sessions, 4) if sessions else 0.0
    return out


def merge_clarity(snapshots, date):
    """URL 행을 상품번호로 접는다. 매칭 실패분은 버리지 않고 따로 센다.

    ⚠ Clarity 행동지표 행의 필드 의미 (한 번 크게 틀렸던 부분):
      sessionsCount                 그 URL의 **전체** 세션 수. 지표 발생 수가 아니다.
      sessionsWithMetricPercentage  그중 해당 행동이 일어난 세션의 비율(%)
      subTotal                      발생 **횟수**

    sessionsCount 를 그대로 더하면 '26번 분노클릭 54건' 같은 숫자가 나오는데,
    실제로는 전 행이 0%였다(= 분노클릭 0건). 세션 수를 지표로 착각한 것이다.
    """
    # 한 날짜에 스냅샷이 여러 개일 수 있다(예약 실행 실패 후 복구분 등, `날짜#2`).
    # 달력일에 가장 잘 맞는 것을 고르고, 동률이면 나중에 받은 것을 쓴다.
    all_snaps = snapshots.get("snapshots") or {}
    cands = [v for k, v in all_snaps.items()
             if (v.get("date_key") or k.split("#")[0]) == date]
    if not cands:
        return {}, {"matched": 0, "unmatched": 0}
    snap = sorted(cands,
                  key=lambda v: (bool(v.get("aligned_to_calendar_day")),
                                 v.get("fetched_at") or ""))[-1]
    if not snap:
        return {}, {"matched": 0, "unmatched": 0}
    call = (snap.get("calls") or {}).get("URL") or {}
    metrics = call.get("metrics") or {}

    per, un = {}, {"matched": 0, "unmatched": 0}

    def rows(name):
        return metrics.get(name) or []

    def _int(x):
        try:
            return int(float(x or 0))
        except (TypeError, ValueError):
            return 0

    # URL별 세션 수를 먼저 잡아둔다. 스크롤 뎁스 가중치로 쓰려면 '그 URL의' 세션 수가
    # 필요하다 — 누적 합계를 가중치로 쓰면 뒤에 온 URL일수록 무겁게 잡혀 평균이 왜곡된다.
    url_sessions = {}
    for r in rows("Traffic"):
        u = r.get("Url") or ""
        n = _int(r.get("totalSessionCount")) or _int(r.get("distinctUserCount"))
        url_sessions[u] = url_sessions.get(u, 0) + n
        pid = C.parse_product_no(u)
        if not pid:
            un["unmatched"] += n
            continue
        un["matched"] += n
        per.setdefault(pid, {"sessions": 0, "scroll_num": 0.0, "scroll_den": 0,
                             "dead": 0, "rage": 0, "quickback": 0})
        per[pid]["sessions"] += n

    for r in rows("ScrollDepth"):
        u = r.get("Url") or ""
        pid = C.parse_product_no(u)
        if not pid or pid not in per:
            continue
        w = max(1, url_sessions.get(u, 1))
        try:
            per[pid]["scroll_num"] += float(r.get("averageScrollDepth") or 0) * w
            per[pid]["scroll_den"] += w
        except (TypeError, ValueError):
            pass

    # 총 체류 대비 실제 활동 시간. 체류가 길어도 활동비율이 낮으면 '켜두고 딴짓'이고,
    # 높으면 '진짜 보는 중'이다. 같은 체류시간이라도 해석이 정반대가 된다.
    for r in rows("EngagementTime"):
        pid = C.parse_product_no(r.get("Url") or "")
        if not pid or pid not in per:
            continue
        per[pid].setdefault("total_time", 0)
        per[pid].setdefault("active_time", 0)
        per[pid]["total_time"] += _int(r.get("totalTime"))
        per[pid]["active_time"] += _int(r.get("activeTime"))

    # 행동지표는 '세션 수 x 발생 비율'로 환산해야 한다(위 docstring 참고).
    for name, key in (("DeadClickCount", "dead"), ("RageClickCount", "rage"),
                      ("QuickbackClick", "quickback")):
        for r in rows(name):
            pid = C.parse_product_no(r.get("Url") or "")
            if not pid or pid not in per:
                continue
            try:
                share = float(r.get("sessionsWithMetricPercentage") or 0) / 100.0
            except (TypeError, ValueError):
                share = 0.0
            per[pid][key] += round(_int(r.get("sessionsCount")) * share)

    out = {}
    for pid, v in per.items():
        out[pid] = {
            "sessions": v["sessions"],
            "avg_scroll_depth": round(v["scroll_num"] / v["scroll_den"], 1)
            if v["scroll_den"] else None,
            # 전부 '해당 행동이 일어난 세션 수'다. 횟수가 아니다.
            "dead_click_sessions": v["dead"],
            "rage_click_sessions": v["rage"],
            "quickback_sessions": v["quickback"],
            "active_ratio": (round(v["active_time"] / v["total_time"], 3)
                             if v.get("total_time") else None),
            "truncated": bool(call.get("truncated")),
        }
    return out, un


def main():
    ga4 = C.load_json(GA4, {"days": {}}).get("days") or {}
    clarity_all = C.load_json(CLARITY, {})
    versions = C.load_json(VERSIONS, {})
    products = C.load_json(PRODUCTS, {})
    cafe24 = C.load_json(CAFE24, {}).get("days") or {}

    if not ga4:
        print("GA4 데이터가 없다. fetch_ga4_pdp.py 를 먼저 돌릴 것.", file=sys.stderr)
        return 1

    out = {"days": {}, "built_at": C.kst_now().strftime("%Y-%m-%d %H:%M:%S KST")}

    for date in sorted(ga4):
        day = ga4[date]
        clar, un_clar = merge_clarity(clarity_all, date)

        rec = {
            "sources": {
                "ga4": "ok",
                # '0원'과 '미수집'을 구분 못 하면 화면이 거짓말을 한다. 반드시 남긴다.
                "clarity": "ok" if clar else "missing",
                "cafe24": "ok" if date in cafe24 else "missing",
            },
            "products": {},
            "unattributed": {
                "ga4_unknown_exit_events": (day.get("unknown") or {}).get("exit_events", 0),
                "clarity_unmatched_sessions": un_clar["unmatched"],
            },
        }

        for pid, p in (day.get("products") or {}).items():
            ver = (versions.get(pid) or {}).get("current") or {}
            total = p.get("section_total") or ver.get("image_count") or 0

            devices, allb = {}, _blank()
            for dev, b in (p.get("devices") or {}).items():
                devices[dev] = _derive(b, total)
                _add(allb, b)
            devices["_all"] = _derive(allb, total)

            # display_urls 는 배너 포함 DOM 순서 그대로라 구간 번호와 1:1로 맞는다.
            # ver["images"] 는 배너를 뺀 '버전 비교용 이름'이라 그림 붙이기에 쓰면
            # 번호가 밀리고, 게다가 copy-<epoch>- 가 벗겨져 URL 복원도 안 된다.
            imgs = (versions.get(pid) or {}).get("display_urls") or []
            sizes = (versions.get(pid) or {}).get("image_bytes") or []
            rec["products"][pid] = {
                "name": p.get("name") or "",
                "slug": (products.get(pid) or {}).get("slug", ""),
                "url": "https://curaelmall.com/product/detail.html?product_no=%s" % pid,
                "section_total": total,
                "version": "v%s.%s" % (ver.get("version", 0), ver.get("minor", 0)),
                # 구간 번호 → 실제 이미지. 이게 있어야 "몇 번째에서 나갔나"가
                # 번호가 아니라 그림으로 보인다.
                "images": imgs,
                "image_bytes": sizes,
                "image_bytes_total": sum(sizes),
                "devices": devices,
                "channels": p.get("channels") or {},
                "clarity": clar.get(pid),
                "sales": ((cafe24.get(date) or {}).get("products") or {}).get(pid),
                "sample": "ok" if devices["_all"]["sessions"] >= MIN_SESSIONS else "low",
            }

            # 금액 환산. 이게 있어야 "이 이미지를 고치면 얼마"가 나온다.
            # 분모는 상세페이지 세션(pdp_exit). 주문은 상세를 안 보고도 발생할 수 있어
            # (재구매·장바구니 직행) 전환율이 100%를 넘을 수 있다 — 그건 오류가 아니라
            # '상세페이지를 거치지 않은 주문'이라는 신호다. 그래서 자르지 않고 그대로 둔다.
            s = rec["products"][pid]["sales"]
            n = devices["_all"]["sessions"]
            if s and n:
                rec["products"][pid]["derived"] = {
                    "cvr": round(s["orders"] / n, 4),
                    "revenue_per_session": round(s["net"] / n),
                    "aov": round(s["net"] / s["orders"]) if s["orders"] else 0,
                }

        out["days"][date] = rec
        n_low = sum(1 for p in rec["products"].values() if p["sample"] == "low")
        print("  %s  상품 %d개 (표본부족 %d) | clarity %s | cafe24 %s"
              % (date, len(rec["products"]), n_low,
                 rec["sources"]["clarity"], rec["sources"]["cafe24"]))

    C.save_json(OUT, out)
    print("%s 저장 — %d일치" % (OUT, len(out["days"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
