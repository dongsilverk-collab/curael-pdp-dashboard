"""카페24 주문 → data/cafe24_pdp_history.json (상품번호별 일 매출)

**로컬 전용이다. Actions 에 넣지 말 것.**
카페24 refresh 토큰은 쓸 때마다 교체되므로 클라우드와 로컬이 같이 돌면 서로의 토큰을
무효화한다(cafe24_pdp_api.py 상단 참고).

실측(2026-08-06):
  · `embed=items` 지원됨 → 주문 1콜에 아이템까지 온다. 주문별 /items 폴백 불필요.
  · 아이템 payment_amount 합계 vs 주문 payment_amount 합계 차이 -0.2%.
    배송비·주문단위 할인이 주문 쪽에만 있어 완전 일치는 원래 안 된다. 목표는 '일치'가
    아니라 '허용 오차 내 추적'이므로 매번 대조해 reconcile_diff_pct 로 남긴다.

취소 처리:
  주문 단위 canceled=="T" 뿐 아니라 **부분취소는 아이템 단위 order_status** 를 봐야 한다.
  N* = 정상(입금·배송 단계), C* = 취소, R* = 반품, E* = 교환.
  gross(취소 포함)와 net(취소 제외)을 둘 다 저장한다 — 나중에 어느 쪽이 필요할지 모른다.

사용:
  python fetch_cafe24_pdp.py                 # 어제~오늘
  python fetch_cafe24_pdp.py --days 30       # 최근 30일 백필
  python fetch_cafe24_pdp.py --date 2026-08-06
"""
import argparse
import sys

import cafe24_pdp_api as API
import pdp_common as C

OUT = "data/cafe24_pdp_history.json"
PAGE = 100
# 하루 주문이 이보다 많으면 잘린다. 조용히 자르지 않고 truncated 로 표시한다.
# (원본 cafe24_api.py:137 은 offset>2000 에서 말없이 break 해서 결손을 알 수 없었다.)
MAX_ORDERS = 5000

CANCEL_PREFIX = ("C", "R")   # 취소 / 반품


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(x):
    try:
        return int(float(x or 0))
    except (TypeError, ValueError):
        return 0


def fetch_day(date):
    """하루치 주문을 상품번호로 접는다."""
    per, offset, truncated = {}, 0, False
    n_orders = 0
    item_sum, order_sum = 0.0, 0.0

    while True:
        res = API.get("/admin/orders", {
            "start_date": date, "end_date": date, "date_type": "pay_date",
            "limit": PAGE, "offset": offset, "embed": "items"})
        orders = res.get("orders") or []
        for o in orders:
            n_orders += 1
            order_canceled = str(o.get("canceled", "F")) == "T"
            order_sum += _f(o.get("payment_amount"))

            for it in (o.get("items") or []):
                pid = str(it.get("product_no") or "").strip()
                if not pid or pid == "0":
                    continue
                amt = _f(it.get("payment_amount"))
                qty = _i(it.get("quantity"))
                item_sum += amt

                st = str(it.get("order_status") or "")
                canceled = order_canceled or st[:1] in CANCEL_PREFIX

                b = per.setdefault(pid, {
                    "name": it.get("product_name") or "",
                    "orders": set(), "units": 0, "gross": 0.0,
                    "net": 0.0, "canceled_units": 0})
                b["orders"].add(str(o.get("order_id")))
                b["units"] += qty
                b["gross"] += amt
                if canceled:
                    b["canceled_units"] += _i(it.get("claim_quantity")) or qty
                else:
                    b["net"] += amt

        if len(orders) < PAGE:
            break
        offset += PAGE
        if offset >= MAX_ORDERS:
            truncated = True
            print("  ! %s 주문이 %d건을 넘어 잘렸다 (MAX_ORDERS)" % (date, MAX_ORDERS),
                  file=sys.stderr)
            break

    out = {}
    for pid, b in per.items():
        out[pid] = {"name": b["name"], "orders": len(b["orders"]),
                    "units": b["units"], "gross": round(b["gross"]),
                    "net": round(b["net"]),
                    "canceled_units": b["canceled_units"]}

    diff = ((item_sum - order_sum) / order_sum * 100.0) if order_sum else 0.0
    meta = {"orders_scanned": n_orders, "truncated": truncated,
            "item_sum": round(item_sum), "order_sum": round(order_sum),
            "reconcile_diff_pct": round(diff, 2)}
    if abs(diff) > 3:
        print("  ! %s 대조 오차 %.1f%% — 아이템 합계와 주문 합계가 벌어졌다" % (date, diff),
              file=sys.stderr)
    return out, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--days", type=int, default=2,
                    help="오늘 포함 최근 N일 (기본 2 = 오늘·어제)")
    args = ap.parse_args()

    dates = [args.date] if args.date else [C.kst_date(i) for i in range(args.days)]

    hist = C.load_json(OUT, {"days": {}})
    hist.setdefault("days", {})

    ok = 0
    for d in sorted(dates):
        try:
            products, meta = fetch_day(d)
        except API.Cafe24Error as e:
            print("  %s 실패: %s" % (d, e), file=sys.stderr)
            continue
        # 주문은 취소·환불로 나중에 바뀐다. GA4와 같은 이유로 덮어쓰기가 맞다.
        hist["days"][d] = {"products": products, "_meta": meta}
        rev = sum(p["net"] for p in products.values())
        print("  %s  주문 %d건 / 상품 %d개 / 순매출 %s원  (대조 %+.1f%%)"
              % (d, meta["orders_scanned"], len(products), f"{rev:,}",
                 meta["reconcile_diff_pct"]))
        ok += 1

    hist["updated_at"] = C.kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    C.save_json(OUT, hist)
    print("%s 저장 — 총 %d일치" % (OUT, len(hist["days"])))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
