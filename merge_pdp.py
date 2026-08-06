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

    pct, drop = {}, {}
    if sessions and total_sections:
        prev = None
        for i in range(1, total_sections + 1):
            p = reach.get(i, 0) / sessions
            pct[str(i)] = round(p, 4)
            if prev is not None:
                drop[str(i)] = round(prev - p, 4)
            prev = p
    out["section_reach_pct"] = pct
    out["section_dropoff"] = drop

    # 낙차 순위는 S02부터 센다. S01 낙차에는 '3초 미만 이탈'(유입 품질·광고 소재 불일치)이
    # 몰리는데 그건 이미지 문제가 아니라 처방이 완전히 다르다. 섞으면 엉뚱한 걸 고치게 된다.
    cand = {int(k): v for k, v in drop.items() if int(k) >= 2}
    if cand:
        i = max(cand, key=lambda k: cand[k])
        out["biggest_drop_section"] = i
        out["biggest_drop_rate"] = cand[i]
        out["biggest_drop_people"] = round(sessions * cand[i])
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
    """URL 행을 상품번호로 접는다. 매칭 실패분은 버리지 않고 따로 센다."""
    snap = (snapshots.get("snapshots") or {}).get(date)
    if not snap:
        return {}, {"matched": 0, "unmatched": 0}
    call = (snap.get("calls") or {}).get("URL") or {}
    metrics = call.get("metrics") or {}

    per, un = {}, {"matched": 0, "unmatched": 0}

    def rows(name):
        return metrics.get(name) or []

    # 세션 수를 먼저 모아 스크롤 뎁스의 가중치로 쓴다.
    for r in rows("Traffic"):
        pid = C.parse_product_no(r.get("Url") or "")
        n = int(r.get("totalSessionCount") or 0) or int(r.get("distinctUserCount") or 0)
        if not pid:
            un["unmatched"] += n
            continue
        un["matched"] += n
        per.setdefault(pid, {"sessions": 0, "scroll_num": 0.0, "scroll_den": 0,
                             "dead": 0, "rage": 0, "quickback": 0})
        per[pid]["sessions"] += n

    for r in rows("ScrollDepth"):
        pid = C.parse_product_no(r.get("Url") or "")
        if not pid or pid not in per:
            continue
        w = max(1, per[pid]["sessions"])
        try:
            per[pid]["scroll_num"] += float(r.get("averageScrollDepth") or 0) * w
            per[pid]["scroll_den"] += w
        except (TypeError, ValueError):
            pass

    for name, key in (("DeadClickCount", "dead"), ("RageClickCount", "rage"),
                      ("QuickbackClick", "quickback")):
        for r in rows(name):
            pid = C.parse_product_no(r.get("Url") or "")
            if not pid or pid not in per:
                continue
            per[pid][key] += int(r.get("sessionsCount") or 0)

    out = {}
    for pid, v in per.items():
        out[pid] = {
            "sessions": v["sessions"],
            "avg_scroll_depth": round(v["scroll_num"] / v["scroll_den"], 1)
            if v["scroll_den"] else None,
            "dead_clicks": v["dead"], "rage_clicks": v["rage"],
            "quickback": v["quickback"],
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
            rec["products"][pid] = {
                "name": p.get("name") or "",
                "slug": (products.get(pid) or {}).get("slug", ""),
                "url": "https://curaelmall.com/product/detail.html?product_no=%s" % pid,
                "section_total": total,
                "version": "v%s.%s" % (ver.get("version", 0), ver.get("minor", 0)),
                # 구간 번호 → 실제 이미지. 이게 있어야 "몇 번째에서 나갔나"가
                # 번호가 아니라 그림으로 보인다.
                "images": imgs,
                "devices": devices,
                "clarity": clar.get(pid),
                "sales": (cafe24.get(date) or {}).get(pid),
                "sample": "ok" if devices["_all"]["sessions"] >= MIN_SESSIONS else "low",
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
