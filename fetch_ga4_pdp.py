"""GA4 → data/ga4_pdp_history.json 일별 적재.

Clarity(fetch_clarity_pdp.py)와 정반대 규칙이라 헷갈리기 쉽다. 이유까지 적어둔다.

  Clarity : append-only.  API가 최근 3일치만 주므로 한 번 놓치면 영구 소실이다.
            이미 있는 날짜는 절대 건드리지 않는다.
  GA4     : 덮어쓰기.    수집 후 약 48시간 동안 수치가 계속 확정된다. 그래서 매 실행마다
            최근 며칠(기본 D-1~D-3)을 다시 받아 통째로 갈아끼운다.

리포트 4종 (계획서 R1~R4):
  R1 sections : pdp_section  상품 x 구간 x 기기   → 도달 곡선
  R2 exits    : pdp_exit     상품 x 이탈구간 x 기기 → 이탈 분포
  R3 summary  : pdp_exit     상품 x 기기 + 합계 측정항목 → 상품별 KPI
  R4 events   : 이벤트 x 상품 x 기기            → 퍼널 분모 + 크롤 대상 상품 목록

사용:
  python fetch_ga4_pdp.py                 # 오늘 + 최근 3일 재수집
  python fetch_ga4_pdp.py --date 2026-08-06
  python fetch_ga4_pdp.py --days 7        # 최근 7일 재수집(백필)
"""
import argparse
import sys
import urllib.parse

import ga4_api
import pdp_common as C

OUT = "data/ga4_pdp_history.json"

# 맞춤 정의 API 이름. GA4 관리 > 맞춤 정의에 등록된 이벤트 매개변수와 1:1로 대응한다.
D_PRODUCT = "customEvent:pdp_product_id"
D_NAME = "customEvent:pdp_product_name"
D_SECTION = "customEvent:pdp_section_label"
D_EXIT = "customEvent:pdp_exit_label"
# 2026-08-20 등록. 메타 광고 URL 매개변수 utm_content(=광고세트 이름)를 담는다.
D_UTM = "customEvent:pdp_utm_content"
D_PERCENT = "customEvent:pdp_percent"   # 2026-08-07 등록. 그 이전 데이터는 비어 있다.
D_ZONE = "customEvent:pdp_zone"         # 2026-08-07 등록. GTM 게시 후부터 값이 들어온다.
D_DEVICE = "deviceCategory"

# 합계로 돌아오는 맞춤 측정항목. 평균은 여기서 내지 않고 merge 단계에서 eventCount로 나눈다.
# (원천에 sum_* 을 그대로 남겨야 기간·기기 합산 시 '평균의 평균' 오류가 안 생긴다.)
#
# 이 중 GA4 '맞춤 측정항목'으로 등록되지 않은 것은 요청에 넣는 순간 리포트 전체가 HTTP 400으로
# 죽는다 — 하나 때문에 그날 수집이 통째로 날아간다. 그래서 첫 실행 때 한 번 걸러낸다.
M_SUMS = [
    ("sum_seconds", "customEvent:pdp_seconds"),
    ("sum_bounce_3s", "customEvent:pdp_bounce_3s"),
    ("sum_saw_cta", "customEvent:pdp_saw_cta"),
    ("sum_saw_review", "customEvent:pdp_saw_review"),
    ("sum_clicked_cart", "customEvent:pdp_clicked_cart"),
    ("sum_clicked_buy", "customEvent:pdp_clicked_buy"),
]

_valid_sums = None   # [(name, api)] — probe_metrics() 가 채운다


def probe_metrics(date, access, prop):
    """등록된 맞춤 측정항목만 남긴다. 전체를 한 번에 시도하고, 실패하면 하나씩 확인한다."""
    global _valid_sums
    if _valid_sums is not None:
        return _valid_sums
    try:
        _rows([D_DEVICE], ["eventCount"] + [a for _, a in M_SUMS],
              date, "pdp_exit", access, prop)
        _valid_sums = list(M_SUMS)
        return _valid_sums

    except ga4_api.GA4Error:
        pass

    good, bad = [], []
    for name, api in M_SUMS:
        try:
            _rows([D_DEVICE], ["eventCount", api], date, "pdp_exit", access, prop)
            good.append((name, api))
        except ga4_api.GA4Error:
            bad.append(api.split(":", 1)[1])
    if bad:
        print("  ! GA4 맞춤 측정항목 미등록으로 건너뜀: %s" % ", ".join(bad),
              file=sys.stderr)
    _valid_sums = good
    return _valid_sums

PDP_EVENTS = ("pdp_scroll", "pdp_section", "pdp_cta_view",
              "pdp_cta_click", "pdp_review", "pdp_exit")


def _rows(dims, mets, date, event=None, access=None, prop=None):
    return ga4_api.run_report(dims, mets, date, date, event_name=event,
                              access=access, prop=prop)


# GA4 는 값이 없을 때 빈 문자열이 아니라 "(not set)" / "(other)" 같은 표기를 준다.
# 이걸 상품번호로 받으면 '(not set)' 이라는 유령 상품이 생긴다(실제로 178세션짜리가
# 만들어졌다). 숫자가 아닌 것은 전부 미귀속으로 보낸다.
def _pid(row):
    v = (row.get(D_PRODUCT) or "").strip()
    return v if v.isdigit() else ""


def _n(row, key):
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _norm_src(v):
    """utm_content 값을 하나의 표기로 모은다.

    같은 광고 세트가 두 형태로 들어온다(실측 2026-08-20):
      숨촉촉_수동형_트랙픽_ 1일3만원_0818
      %EC%88%A8%EC%B4%89%EC%B4%89_%EC%88%98%EB%8F%99%ED%...
    브라우저·유입 경로에 따라 한글이 퍼센트 인코딩되기도 해서, 안 풀면 같은
    소재가 둘로 갈려 표본이 반토막 난다.

    숫자만 들어오는 경우도 있다(광고 ID). 이름이 아니라 ID 라는 걸 화면에서
    알아볼 수 있게 표시를 붙인다 — 조용히 섞으면 나중에 원인을 못 찾는다.
    """
    s = (v or "").strip()
    if not s or s == "(not set)":
        return "_비광고"
    if "%" in s:
        # GA4 가 값을 100바이트에서 자르는 탓에 퍼센트 인코딩이 중간에 끊긴 채로
        # 오기도 한다("...1일3%EB"). errors="replace" 로 풀면 깨진 문자가 남아
        # 같은 세트가 둘로 갈리므로, 꼬리의 불완전한 조각은 잘라낸다.
        try:
            s = urllib.parse.unquote(s, errors="strict")
        except (UnicodeDecodeError, ValueError):
            cut = s
            for _ in range(6):
                cut = cut[:cut.rfind("%")] if "%" in cut else cut
                try:
                    s = urllib.parse.unquote(cut, errors="strict") + "…"
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
            else:
                s = urllib.parse.unquote(s, errors="replace")
    s = " ".join(s.split())          # 연속 공백·언더바 뒤 공백 정리
    if s.isdigit():
        return "ID %s (이름 미설정)" % s
    return s


def fetch_day(date, access=None, prop=None):
    """하루치를 4개 리포트로 받아 하나의 dict로 만든다."""
    access = access or ga4_api.token()
    prop = prop or ga4_api.property_id()

    day = {"date": date, "products": {}, "unknown": {}}

    def bucket(pid, dev):
        p = day["products"].setdefault(pid, {"name": "", "devices": {}})
        return p["devices"].setdefault(dev, {
            "section_reach": {}, "exit_hist": {}, "events": {},
            "exit_events": 0,
        })

    # ---- R1 구간 도달 ----
    for r in _rows([D_PRODUCT, D_NAME, D_SECTION, D_DEVICE], ["eventCount"],
                   date, "pdp_section", access, prop):
        pid = _pid(r)
        if not pid:
            day["unknown"]["section_events"] = \
                day["unknown"].get("section_events", 0) + _n(r, "eventCount")
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        day["products"][pid]["name"] = r.get(D_NAME) or ""
        # 라벨 'S03/20' → 인덱스 3. 키를 정수로 두어야 버전이 바뀌어도 비교가 된다.
        lab = r.get(D_SECTION) or ""
        i = _section_index(lab)
        if i is not None:
            b["section_reach"][str(i)] = b["section_reach"].get(str(i), 0) + _n(r, "eventCount")
            tot = _section_total(lab)
            if tot:
                day["products"][pid]["section_total"] = tot

    # ---- R2 이탈 분포 ----
    for r in _rows([D_PRODUCT, D_NAME, D_EXIT, D_DEVICE], ["eventCount"],
                   date, "pdp_exit", access, prop):
        pid = _pid(r)
        if not pid:
            day["unknown"]["exit_events"] = \
                day["unknown"].get("exit_events", 0) + _n(r, "eventCount")
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        day["products"][pid]["name"] = r.get(D_NAME) or day["products"][pid]["name"]
        lab = r.get(D_EXIT) or ""
        i = _section_index(lab)
        key = str(i) if i is not None else "?"
        b["exit_hist"][key] = b["exit_hist"].get(key, 0) + _n(r, "eventCount")

    # ---- R9 유입(광고 소재)별 이탈 분포 ----
    # R2 와 같은 이벤트를 보지만 차원에 utm_content 를 더한다. R2 를 고치지 않고
    # 따로 받는 이유는, 맞춤 측정기준이 비어 있는 기간(등록 전 데이터)에도
    # 기존 집계가 그대로 남아야 하기 때문이다.
    for r in _rows([D_PRODUCT, D_UTM, D_EXIT], ["eventCount"],
                   date, "pdp_exit", access, prop):
        pid = _pid(r)
        if not pid:
            continue
        src = _norm_src(r.get(D_UTM))
        p = day["products"].setdefault(pid, {"name": "", "devices": {}})
        by = p.setdefault("by_source", {}).setdefault(src, {"exit_hist": {}, "exits": 0})
        i = _section_index(r.get(D_EXIT) or "")
        key = str(i) if i is not None else "?"
        n = _n(r, "eventCount")
        by["exit_hist"][key] = by["exit_hist"].get(key, 0) + n
        by["exits"] += n

    # ---- R10 유입 채널별 이탈 분포 ----
    # R9(utm_content)는 광고에 파라미터를 붙인 뒤부터만 값이 있고 표본도 얇다.
    # 채널 축은 GA4 가 알아서 분류하므로 과거분까지 전부 쓸 수 있다 —
    # "광고로 온 사람 vs 검색으로 온 사람이 어디까지 보나"는 이쪽이 정본이다.
    for r in _rows([D_PRODUCT, "sessionDefaultChannelGroup", D_EXIT], ["eventCount"],
                   date, "pdp_exit", access, prop):
        pid = _pid(r)
        if not pid:
            continue
        ch = r.get("sessionDefaultChannelGroup") or "(미분류)"
        p = day["products"].setdefault(pid, {"name": "", "devices": {}})
        by = p.setdefault("by_channel", {}).setdefault(ch, {"exit_hist": {}, "exits": 0})
        i = _section_index(r.get(D_EXIT) or "")
        key = str(i) if i is not None else "?"
        n = _n(r, "eventCount")
        by["exit_hist"][key] = by["exit_hist"].get(key, 0) + n
        by["exits"] += n

    # ---- R3 상품별 합계 KPI ----
    sums = probe_metrics(date, access, prop)
    have = {api for _, api in sums}
    day["missing_metrics"] = [api.split(":", 1)[1]
                              for _, api in M_SUMS if api not in have]
    mets = ["eventCount"] + [api for _, api in sums]
    for r in _rows([D_PRODUCT, D_DEVICE], mets, date, "pdp_exit", access, prop):
        pid = _pid(r)
        if not pid:
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        b["exit_events"] = _n(r, "eventCount")
        for name, api in sums:
            b[name] = _n(r, api)

    # ---- R8 진입/즉시이탈 ----
    # "그냥 들어왔다 나가는 사람"은 우리 추적에 안 잡힌다. GTM 태그가 DOM Ready 에
    # 뜨는데 그 전에 나가버리기 때문이다(pdp_bounce_3s 가 전 상품 0인 이유).
    # 그래서 세션 단위 GA4 지표를 쓴다 — page_view 기반이라 훨씬 일찍 잡힌다.
    # landingPage 를 상품번호로 파싱해 상품에 붙인다.
    for r in _rows(["landingPagePlusQueryString"], ["sessions", "engagedSessions"],
                   date, None, access, prop):
        lp = r.get("landingPagePlusQueryString") or ""
        pid = C.parse_product_no(lp)
        if not pid:
            continue
        p = day["products"].setdefault(pid, {"name": "", "devices": {}})
        e = p.setdefault("entry", {"sessions": 0, "engaged": 0})
        e["sessions"] += _n(r, "sessions")
        e["engaged"] += _n(r, "engagedSessions")

    # ---- R7 클릭 영역 ----
    # Clarity 클릭 히트맵은 API 가 없어 매번 사람이 로그인해 읽어야 한다. 같은 정보를
    # 직접 세면 자동으로 쌓인다. 스크립트가 **영역당 세션 1회만** 쏘므로 여기 숫자는
    # '몇 명이 그 영역을 건드렸나'다. 총 클릭수가 아니다 — 소수 사용자의 연타에
    # 휘둘리지 않게 하려는 의도적 선택이다.
    for r in _rows([D_PRODUCT, D_ZONE, D_DEVICE], ["eventCount"],
                   date, "pdp_zone_click", access, prop):
        pid = _pid(r)
        z = (r.get(D_ZONE) or "").strip()
        if not pid or not z or z.startswith("("):
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        b.setdefault("zone_clicks", {})
        b["zone_clicks"][z] = b["zone_clicks"].get(z, 0) + _n(r, "eventCount")

    # ---- R6 스크롤 도달 곡선 ----
    # Clarity 대시보드의 '데이터 스크롤' 표와 같은 성격인데, 그 표는 API 로 못 받는다
    # (Clarity API 는 평균 스크롤 깊이 하나만 준다). 우리 추적 스크립트가 이미
    # 10/25/50/75/90/100% 에서 pdp_scroll 을 쏘고 있으므로 그걸 상품별로 쪼갠다.
    #
    # ⚠ 분모가 다르다. Clarity 는 **페이지 전체** 기준, 이쪽은 **#prdDetail 상세영역**
    # 기준이다. 상단(가격·옵션·리뷰탭)이 빠져 있어 같은 25%라도 가리키는 위치가 다르다.
    for r in _rows([D_PRODUCT, D_PERCENT, D_DEVICE], ["eventCount"],
                   date, "pdp_scroll", access, prop):
        pid = _pid(r)
        pc = (r.get(D_PERCENT) or "").strip()
        if not pid or not pc.isdigit():
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        b.setdefault("scroll_reach", {})
        b["scroll_reach"][pc] = b["scroll_reach"].get(pc, 0) + _n(r, "eventCount")

    # ---- R5 유입 채널 ----
    # sessionSource/Medium 은 이벤트 단위 맞춤측정기준과 엮으면 카디널리티 때문에
    # 82%가 (not set)/(data not available) 로 뭉개진다. 채널그룹은 불명 0%다.
    # 대신 분류 규칙이 없는 트래픽이 Unassigned 로 몰리는데, 그건 숨기지 않고 그대로 보여준다.
    for r in _rows([D_PRODUCT, "sessionDefaultChannelGroup"], ["eventCount"],
                   date, "pdp_exit", access, prop):
        pid = _pid(r)
        if not pid:
            continue
        p = day["products"].setdefault(pid, {"name": "", "devices": {}})
        ch = p.setdefault("channels", {})
        name = r.get("sessionDefaultChannelGroup") or "(미분류)"
        ch[name] = ch.get(name, 0) + _n(r, "eventCount")

    # ---- R4 이벤트별 카운트 (퍼널 분모) ----
    for r in _rows(["eventName", D_PRODUCT, D_DEVICE], ["eventCount"],
                   date, None, access, prop):
        ev = r.get("eventName") or ""
        if ev not in PDP_EVENTS:
            continue
        pid = _pid(r)
        if not pid:
            continue
        b = bucket(pid, r.get(D_DEVICE) or "unknown")
        b["events"][ev] = b["events"].get(ev, 0) + _n(r, "eventCount")

    return day


def _section_index(label):
    """'S03/20' → 3,  'S00/20' → 0,  '' → None."""
    if not label or not label.startswith("S"):
        return None
    head = label.split("/")[0][1:]
    return int(head) if head.isdigit() else None


def _section_total(label):
    if "/" not in label:
        return 0
    tail = label.split("/", 1)[1]
    return int(tail) if tail.isdigit() else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="이 날짜만 수집 (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=4,
                    help="오늘 포함 최근 N일 재수집 (기본 4 = 오늘~D-3)")
    args = ap.parse_args()

    dates = [args.date] if args.date else [C.kst_date(i) for i in range(args.days)]

    hist = C.load_json(OUT, {"days": {}})
    hist.setdefault("days", {})

    try:
        access = ga4_api.token()
        prop = ga4_api.property_id()
    except ga4_api.GA4Error as e:
        print("GA4 인증 실패: %s" % e, file=sys.stderr)
        return 1

    ok = 0
    for d in sorted(dates):
        try:
            day = fetch_day(d, access, prop)
        except ga4_api.GA4Error as e:
            print("  %s 실패: %s" % (d, e), file=sys.stderr)
            continue
        # GA4는 덮어쓰기가 정답이다(위 주석 참고).
        hist["days"][d] = day
        n_p = len(day["products"])
        n_e = sum(b.get("exit_events", 0)
                  for p in day["products"].values()
                  for b in p["devices"].values())
        print("  %s  상품 %d개 / 이탈 %d건%s"
              % (d, n_p, n_e,
                 ("  (미식별 %d)" % day["unknown"]["exit_events"])
                 if day["unknown"].get("exit_events") else ""))
        ok += 1

    hist["updated_at"] = C.kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    C.save_json(OUT, hist)
    print("%s 저장 — 총 %d일치" % (OUT, len(hist["days"])))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
