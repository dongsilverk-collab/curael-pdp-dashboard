"""data/pdp_daily.json → docs/index.html + docs/product-<번호>.html

화면 두 장으로 나눈다.
  index        전체 상품을 한눈에 비교 (곡선 모양으로 훑는다)
  product-N    한 상품의 구간별 상세 (썸네일로 '고칠 이미지'를 특정한다)

상품별로 파일을 나누는 이유: 상품당 이미지가 최대 20장이라 한 페이지에 다 넣으면
`<img>` 200개가 넘고, 앵커 방식은 뒤로가기가 안 돼 목록↔상세 왕복이 깨진다.

정직성 장치(이게 없으면 화면이 거짓말을 한다):
  · 모든 비율에 분모를 병기한다
  · 표본 30세션 미만은 순위에서 빼고 아래 별도 구역에 회색으로 둔다
  · 미수집(회색 –)과 실제 0(검은 0)을 구분한다
  · 수집 경과가 7일 미만이면 상단에 배너를 띄우고 처방 문구를 아예 만들지 않는다
"""
import os
import sys

import pdp_charts as G
import pdp_common as C

SRC = "data/pdp_daily.json"
OUT = "docs"
MIN_SESSIONS = 30
ADVICE_MIN_DAYS = 7      # 이 미만이면 처방 카드를 만들지 않는다
ADVICE_MIN_PEOPLE = 50   # 손실 인원이 이보다 적으면 고칠 가치가 없다


def curve(p, dev="_all"):
    """구간 도달률을 1..N 순서의 리스트로. 그래프용."""
    d = p["devices"][dev]
    pcts = d.get("section_reach_pct") or {}
    n = p.get("section_total") or 0
    return [pcts.get(str(i), 0.0) for i in range(1, n + 1)]


SOURCE_KO = {"ga4": "GA4 (행동)", "clarity": "Clarity", "cafe24": "카페24 (매출)"}
SOURCE_WHY = {
    "ga4": "Actions 워크플로 pdp daily 로그를 확인하세요.",
    "clarity": "Actions 워크플로 clarity daily 로그를 확인하세요. "
               "하루 10회 호출 한도를 넘겼을 수 있습니다.",
    "cafe24": "PC가 켜져 있어야 돕니다. 켜고 <code>collect_pdp.bat</code>을 실행하면 "
              "빠진 날짜가 자동으로 채워집니다.",
}


def freshness_block(rec):
    """어느 소스가 며칠째 안 들어오는지 화면 맨 위에 크게 띄운다.

    2026-08-13~17 에 카페24가 5일, Clarity 가 3일 끊겼는데 화면에는 아무 표시가 없어
    대표가 매출이 0인 줄 알았다. 조용히 실패하는 게 실패 자체보다 나쁘다 —
    숫자가 틀린 줄 모르고 판단하게 되기 때문이다.
    """
    fresh = rec.get("source_fresh") or {}
    if not fresh:
        return ""
    bad = [(k, v) for k, v in fresh.items() if v.get("stale_days", 0) >= 1]
    if not bad:
        return ('<div class="ok">세 소스 모두 정상 수집 중입니다 '
                '— GA4 · Clarity · 카페24.</div>')
    bad.sort(key=lambda kv: -kv[1]["stale_days"])
    items = "".join(
        "<li><b>%s</b>가 <b>%d일째</b> 안 들어옵니다"
        "(마지막 %s). %s</li>"
        % (SOURCE_KO.get(k, k), v["stale_days"], v.get("last") or "기록 없음",
           SOURCE_WHY.get(k, ""))
        for k, v in bad)
    return ('<div class="alert"><b>⚠ 수집이 끊긴 소스가 있습니다.</b>'
            '<ul>%s</ul>'
            '끊긴 소스의 숫자는 <b>0이 아니라 "모름"</b>입니다. '
            '그 항목으로 판단하지 마세요.</div>' % items)


def build_index(date, rec, days_collected):
    ps = rec["products"]
    ranked, low = [], []
    for pid, p in ps.items():
        (ranked if p["sample"] == "ok" else low).append((pid, p))

    # 정렬 기준은 '손실 인원'이지 낙차율이 아니다. 낙차 70%라도 세션 20이면
    # 고칠 가치가 없다. 명 수는 설명 없이 이해되는 유일한 단위다.
    key = lambda kv: -kv[1]["devices"]["_all"].get("biggest_drop_people", 0)
    ranked.sort(key=key)
    low.sort(key=lambda kv: -kv[1]["devices"]["_all"]["sessions"])

    tot_sessions = sum(p["devices"]["_all"]["sessions"] for p in ps.values())
    s00 = sum(p["devices"]["_all"]["exit_s00"] for p in ps.values())
    done = sum(p["devices"]["_all"]["completed"] for p in ps.values())
    scrolls = [p["clarity"]["avg_scroll_depth"] for p in ps.values()
               if p.get("clarity") and p["clarity"].get("avg_scroll_depth")]

    out = ["<h1>큐라엘몰 상세페이지 이탈 분석</h1>",
           '<p class="sub">%s 합산 · %d일치 · 생성 %s</p>'
           % (date, days_collected, C.kst_now().strftime("%m-%d %H:%M"))]

    if days_collected < ADVICE_MIN_DAYS:
        out.append(
            '<div class="note"><b>수집 %d일치입니다.</b> 방향만 참고하세요. '
            '표본이 쌓이기 전의 숫자로 상세페이지를 고치면 엉뚱한 곳을 고치게 됩니다. '
            '처방은 7일치가 모이면 표시됩니다.</div>' % days_collected)

    out.append(freshness_block(rec))

    revenue = sum((p.get("sales") or {}).get("net", 0) for p in ps.values())
    orders = sum((p.get("sales") or {}).get("orders", 0) for p in ps.values())

    out.append('<div class="kpis">')
    out.append(G.kpi("상세페이지 세션", f"{tot_sessions:,}", "pdp_exit 기준"))
    out.append(G.kpi("상세 미도달", G.pct(s00 / tot_sessions) if tot_sessions else "–",
                     "%s명 · 이미지를 못 봄" % f"{s00:,}"))
    out.append(G.kpi("끝까지 봄", G.pct(done / tot_sessions) if tot_sessions else "–",
                     "%s명" % f"{done:,}"))
    if rec["sources"]["cafe24"] == "ok":
        out.append(G.kpi("세션당 매출", "%s원" % f"{round(revenue / tot_sessions):,}"
                         if tot_sessions else "–",
                         "순매출 %s원 · 주문 %d건" % (f"{revenue:,}", orders)))
    out.append(G.kpi("Clarity 스크롤", "%.0f%%" % (sum(scrolls) / len(scrolls))
                     if scrolls else "–", "상품 평균"))
    out.append("</div>")

    out.append("<h2>상품별 이탈 프로필</h2>")
    out.append('<div class="scroll"><table><tr>'
               '<th></th><th>상품</th><th class="num">세션</th>'
               '<th>구간 도달 곡선</th><th class="num">미도달</th>'
               '<th class="num">첫 구간 이탈</th><th class="num">완독</th>'
               '<th class="num">최대 낙차</th><th class="num">순매출</th>'
               '<th class="num">세션당</th></tr>')

    def row(pid, p, dim=False):
        a = p["devices"]["_all"]
        n = a["sessions"]
        s, dv = p.get("sales"), p.get("derived") or {}
        # 미수집(회색 –)과 실제 0(검은 0)을 구분한다. 섞이면 화면이 거짓말을 한다.
        if rec["sources"]["cafe24"] != "ok":
            money = rps = '<span class="muted">–</span>'
        elif s:
            money = "%s원" % f"{s['net']:,}"
            rps = "%s원" % f"{dv.get('revenue_per_session', 0):,}"
        else:
            money, rps = "0원", "0원"
        thumb = ('<img class="thumb" loading="lazy" src="%s" alt="">' % G.esc(p["images"][0])
                 if p.get("images") else '<div class="thumb"></div>')
        drop = ("S%02d <span class='muted'>-%s</span>"
                % (a["biggest_drop_section"], G.pct(a["biggest_drop_rate"]))
                if a.get("biggest_drop_section") else '<span class="muted">–</span>')
        return (
            '<tr class="%s"><td>%s</td>'
            '<td><a href="product-%s.html">%s</a>%s<br>'
            '<span class="muted">%s번 · 이미지 %s장 · %s</span></td>'
            '<td class="num">%s</td><td>%s</td>'
            '<td class="num">%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td></tr>'
            % ("dim" if dim else "", thumb, G.esc(pid), G.esc(p["name"][:34]),
               G.sample_badge(n, MIN_SESSIONS), G.esc(pid),
               p["section_total"] or "–", G.esc(p["version"]),
               f"{n:,}", G.sparkline(curve(p)),
               G.pct(a["s00_rate"]), G.pct(a["s01_rate"]),
               G.pct(a["done_rate"]), drop, money, rps))

    if ranked:
        out += [row(pid, p) for pid, p in ranked]
    else:
        out.append('<tr><td colspan="10" class="muted">'
                   '표본 %d세션 이상인 상품이 아직 없습니다.</td></tr>' % MIN_SESSIONS)
    out.append("</table></div>")

    if low:
        out.append("<h2>표본 모으는 중 <span class='muted' "
                   "style='font-weight:400;font-size:13px'>(%d세션 미만)</span></h2>"
                   % MIN_SESSIONS)
        out.append('<div class="scroll"><table><tr>'
                   '<th></th><th>상품</th><th class="num">세션</th>'
                   '<th>구간 도달 곡선</th><th class="num">미도달</th>'
                   '<th class="num">첫 구간 이탈</th><th class="num">완독</th>'
                   '<th class="num">최대 낙차</th><th class="num">순매출</th>'
                   '<th class="num">세션당</th></tr>')
        out += [row(pid, p, dim=True) for pid, p in low]
        out.append("</table></div>")

    un = rec["unattributed"]
    out.append('<footer>상품 미귀속: GA4 이탈 %s건 · Clarity 세션 %s건. '
               'GA4 미귀속은 추적 스크립트 v1.2 게시(2026-08-06) 이전 세션입니다.</footer>'
               % (f"{un['ga4_unknown_exit_events']:,}",
                  f"{un['clarity_unmatched_sessions']:,}"))
    return G.page("큐라엘몰 상세페이지 이탈 분석", "".join(out))


# 유료 지면. by_channel 키와 정확히 일치해야 한다.
PAID_CHANNELS = {"Paid Social", "Cross-network", "Paid Search", "Paid Shopping",
                 "Paid Video", "Paid Other", "Display"}

CHANNEL_KO = {
    "Direct": "직접 방문", "Organic Search": "검색(자연)", "Paid Search": "검색(광고)",
    "Organic Social": "SNS(자연)", "Paid Social": "SNS(광고)",
    "Organic Video": "영상(자연)", "Paid Video": "영상(광고)",
    "Organic Shopping": "쇼핑(자연)", "Paid Shopping": "쇼핑(광고)",
    "Cross-network": "크로스네트워크", "Email": "이메일", "Referral": "타사이트 유입",
    "Unassigned": "미분류", "(미분류)": "미분류",
}


def entry_block(p):
    """들어와서 바로 나간 사람 / 남은 사람 / 상세까지 본 사람.

    **분모가 두 개다.** 진입 세션은 GA4 세션 지표(page_view 기반), 아래 단계는 우리
    추적(pdp_exit). 우리 태그는 DOM Ready 에 뜨므로 그 전에 나간 사람은 진입에만 잡힌다
    — pdp_bounce_3s 가 전 상품 0으로 나온 이유가 이것이다.
    두 수를 한 표에 섞어 비율을 내면 틀리므로 단계마다 출처를 밝힌다.
    """
    e = p.get("entry") or {}
    n_entry = e.get("sessions", 0)
    if not n_entry:
        return ""
    engaged = e.get("engaged", 0)
    bounced = n_entry - engaged
    a = p["devices"]["_all"]
    n_tr = a["sessions"]
    reached = n_tr - a["exit_s00"]
    s = p.get("sales") or {}

    rows = [
        ("이 상품으로 들어옴", n_entry, n_entry, "GA4 세션"),
        ("바로 안 나가고 머묾", engaged, n_entry, "GA4 세션 · 10초 이상 또는 2페이지 이상"),
        ("상세 이미지까지 봄", reached, n_tr, "우리 추적 · 분모가 위와 다름"),
        ("끝까지 봄", a["completed"], n_tr, "우리 추적"),
    ]
    if s:
        rows.append(("주문", s.get("orders", 0), n_entry, "카페24 주문"))

    out = ["<h2>들어와서 어디까지 갔나</h2>",
           '<p class="sub">위 두 줄은 GA4 세션 기준, 아래는 우리 추적 기준이라 '
           '<b>분모가 다릅니다</b>. 우리 태그는 페이지가 어느 정도 뜬 뒤 실행되므로 '
           '그 전에 나간 사람은 위 두 줄에만 잡힙니다.</p>',
           '<div class="scroll"><table><tr><th>단계</th><th>비율</th>'
           '<th class="num">세션</th><th>출처</th></tr>']
    for label, v, base, src in rows:
        out.append('<tr><td>%s</td>'
                   '<td class="bar">%s<span class="barval">%s</span></td>'
                   '<td class="num">%s</td><td class="muted">%s</td></tr>'
                   % (G.esc(label), G.reach_bar((v / base) if base else 0, 0),
                      G.frac(v, base), f"{v:,}", G.esc(src)))
    out.append("</table></div>")
    if n_entry:
        out.append('<div class="note">들어오자마자 나간 사람이 <b>%s</b>(%d명)입니다. '
                   '나머지 %d명이 상세페이지에서 머물다 나가거나 삽니다.</div>'
                   % (G.pct(bounced / n_entry), bounced, engaged))
    return "".join(out)


def zone_block(p):
    """어느 영역을 만졌나. Clarity 클릭 히트맵을 대체한다.

    숫자는 '그 영역을 건드린 세션 수'다. 총 클릭수가 아니다 — 추적 스크립트가
    영역당 세션 1회만 쏘기 때문이고, 그래야 소수 사용자의 연타에 안 휘둘린다.
    """
    a = p["devices"]["_all"]
    z = a.get("zone_clicks") or {}
    n = a["sessions"]
    if not z or not n:
        return ""
    rows = sorted(z.items(), key=lambda kv: -kv[1])
    out = ["<h2>어느 영역을 만졌나</h2>",
           '<p class="sub">그 영역을 <b>건드린 세션 수</b>입니다(총 클릭수 아님). '
           '한 사람이 열 번 눌러도 1로 셉니다.</p>',
           '<div class="scroll"><table><tr><th>영역</th><th>비율</th>'
           '<th class="num">세션</th></tr>']
    top = rows[0][1] if rows else 1
    for name, c in rows:
        out.append('<tr><td>%s</td>'
                   '<td class="bar">%s<span class="barval">%s</span></td>'
                   '<td class="num">%s</td></tr>'
                   % (G.esc(name), G.reach_bar(c / top, 0),
                      G.pct(c / n), f"{c:,}"))
    out.append("</table></div>")
    return "".join(out)


def scroll_block(p):
    """스크롤 도달 곡선. Clarity 대시보드의 '데이터 스크롤' 표와 같은 성격.

    Clarity API 는 평균값 하나만 주므로 그 표는 자동화할 수 없다. 대신 우리
    추적 스크립트가 쏘는 pdp_scroll 을 쓴다 — 상품별로 갈라지고 매일 자동으로 쌓인다.

    분모가 다르다는 점은 화면에 명시한다. Clarity 는 페이지 전체, 이쪽은 상세영역이다.
    """
    a = p["devices"]["_all"]
    curve = a.get("scroll_curve") or {}
    pct = a.get("scroll_curve_pct") or {}
    n = a["sessions"]
    if not curve or not n:
        return ""
    out = ["<h2>상세영역 스크롤 도달</h2>",
           '<p class="sub">상세 이미지 영역(#prdDetail)을 100%로 봤을 때의 도달률입니다. '
           'Clarity의 스크롤 수치는 <b>페이지 전체</b> 기준이라 값이 다릅니다.</p>',
           '<div class="scroll"><table><tr><th>지점</th><th>도달률</th>'
           '<th class="num">여기서 멈춤</th></tr>']
    keys = sorted(curve, key=int)
    for i, k in enumerate(keys):
        v, r = curve[k], pct.get(k, 0)
        nxt = curve[keys[i + 1]] if i + 1 < len(keys) else 0
        out.append('<tr><td>%s%%</td>'
                   '<td class="bar">%s<span class="barval">%s</span></td>'
                   '<td class="num">%s</td></tr>'
                   % (G.esc(k), G.reach_bar(r, ((v - nxt) / n) if n else 0),
                      G.frac(v, n),
                      ("%d명" % (v - nxt)) if v - nxt > 0 else
                      '<span class="muted">–</span>'))
    out.append("</table></div>")
    return "".join(out)


def _depth_rows(bucket, total_sec, label_fn):
    """유입 묶음 하나를 표 한 줄로. 어디까지 보고 나갔는지가 핵심이다."""
    out = []
    for key, v in sorted(bucket.items(), key=lambda kv: -kv[1]["exits"]):
        ex = {int(k): n for k, n in (v.get("exit_hist") or {}).items() if k.isdigit()}
        tot = sum(ex.values())
        if not tot:
            continue
        # S00 = 상세영역에 들어서기도 전에 나간 사람. 이걸 평균에 섞으면
        # '유입이 나쁜 것'과 '페이지가 나쁜 것'이 구분되지 않는다.
        pre = ex.get(0, 0)
        inside = {i: n for i, n in ex.items() if i >= 1}
        n_in = sum(inside.values())
        depth = (sum(i * n for i, n in inside.items()) / n_in / total_sec
                 if n_in and total_sec else None)
        done = inside.get(total_sec, 0) / n_in if n_in and total_sec else 0
        out.append({
            "name": label_fn(key), "exits": v["exits"],
            "pre_rate": pre / tot, "depth": depth, "done": done, "n_in": n_in,
        })
    return out


def _depth_table(rows, head):
    if not rows:
        return ""
    o = ['<div class="scroll"><table><tr><th>%s</th>'
         '<th class="num">이탈</th><th class="num">상세 진입 전</th>'
         '<th class="num">평균 도달</th><th class="num">완독</th>'
         '<th>도달 분포</th></tr>' % head]
    for r in rows:
        low = ' class="dim"' if r["n_in"] < 30 else ""
        badge = G.sample_badge(r["n_in"])
        o.append('<tr%s><td>%s%s</td><td class="num">%s</td>'
                 '<td class="num">%s</td><td class="num">%s</td>'
                 '<td class="num">%s</td><td class="bar">%s</td></tr>'
                 % (low, G.esc(r["name"][:40]), badge, f'{r["exits"]:,}',
                    G.pct(r["pre_rate"]),
                    G.pct(r["depth"]) if r["depth"] is not None else "–",
                    G.pct(r["done"]),
                    G.reach_bar(r["depth"] or 0, r["pre_rate"])))
    o.append("</table></div>")
    return "".join(o)


def verdict_block(pid, p, days):
    """다음에 무엇을 할지 한 줄로 좁힌다.

    보고가 목적이 아니다. 이 화면을 연 사람이 '그래서 뭘 하지'를 들고
    나가야 한다. 그래서 지표를 여럿 늘어놓지 않고 하나로 좁힌다 —
    동시에 여러 개를 손대면 다음에 원인 귀속이 불가능해진다.

    ⚠️ 여기서 판정할 수 있는 것은 **랜딩 쪽(CVR·상세 도달)** 뿐이다.
       CTR·CPM·CPA 는 광고비와 노출 수가 있어야 하는데 그건 메타에 있고
       이 파이프라인에 안 들어온다. 소재를 끄라 켜라는 판정은 여기서 하지 않는다.
    """
    PAID = PAID_CHANNELS
    ds = sorted(days)
    cur = ad = pre = None
    hist = []
    for d in ds:
        pr = (days[d].get("products") or {}).get(pid)
        if not pr:
            continue
        a = (pr.get("devices") or {}).get("_all") or {}
        if not a.get("sessions"):
            continue
        bc = pr.get("by_channel") or {}
        t = sum(v.get("exits", 0) for v in bc.values())
        share = (sum(v.get("exits", 0) for k, v in bc.items() if k in PAID) / t) if t else None
        hist.append({"d": d, "ses": a["sessions"], "s00": a.get("s00_rate") or 0,
                     "done": a.get("done_rate") or 0, "ad": share,
                     "orders": (pr.get("sales") or {}).get("orders") or 0})
    if len(hist) < 4:
        return ""

    # 채널을 모르는 날은 양쪽 어디에도 넣지 않는다. 광고일지 아닐지 모르는 것을
    # 비광고로 세면 비교 기준선 자체가 오염된다.
    adays = [h for h in hist if h["ad"] is not None and h["ad"] >= 0.3]
    orgs = [h for h in hist if h["ad"] is not None and h["ad"] < 0.3 and h["ses"] >= 10]
    if not adays:
        return ""

    # 표본 게이트. 광고 3일 미만이거나 세션이 얇으면 판정하지 않는다.
    n_ses = sum(h["ses"] for h in adays)
    if len(adays) < 3 or n_ses < 100:
        return ('<div class="verdict warn"><h3>아직 판정하지 않습니다</h3>'
                '<p class="why">광고 집행 %d일 · 세션 %s건입니다. '
                '3일 이상 그리고 100세션 이상 쌓여야 방향이 잡힙니다. '
                '지금 숫자로 소재를 끄거나 페이지를 되돌리면 노이즈에 반응하는 것입니다.</p>'
                '</div>' % (len(adays), f"{n_ses:,}"))

    ad_s00 = sum(h["s00"] * h["ses"] for h in adays) / n_ses
    org_s00 = (sum(h["s00"] * h["ses"] for h in orgs) / sum(h["ses"] for h in orgs)
               if orgs else None)
    orders = sum(h["orders"] for h in adays)
    cvr = orders / n_ses if n_ses else 0

    # 판정 — 위에서 아래로, 하나가 걸리면 멈춘다.
    if org_s00 is not None and ad_s00 - org_s00 >= 0.25:
        kind, head = "bad", "광고 유입이 상세페이지에 닿지 못합니다"
        why = ("광고로 들어온 사람의 <b>%s</b>가 상세 이미지를 한 장도 못 보고 나갑니다. "
               "비광고 유입은 %s입니다. 같은 페이지인데 <b>%.0f%%p 차이</b>가 납니다. "
               "페이지가 나쁜 게 아니라 광고가 약속한 것과 페이지 첫 화면이 어긋난 것입니다."
               % (G.pct(ad_s00), G.pct(org_s00), (ad_s00 - org_s00) * 100))
        do = ("이번 기간 지표 <b>하나만</b> 잡습니다 — <b>광고 유입의 상세 미도달률 "
              "%s → %s</b>. 상단(상품명 아래 문구·첫 상세 이미지)을 광고 카피와 "
              "같은 말로 맞추는 것이 1순위입니다. 소재를 끄는 판단은 아직 이릅니다."
              % (G.pct(ad_s00), G.pct(max(org_s00, ad_s00 - 0.15))))
    elif cvr < 0.005 and n_ses >= 300:
        kind, head = "bad", "상세페이지까지는 오는데 사지 않습니다"
        why = ("광고 유입 %s세션에 주문 %d건, 전환율 %s입니다. "
               "상세 미도달은 %s로 유입 자체는 문제가 아닙니다."
               % (f"{n_ses:,}", orders, G.pct(cvr), G.pct(ad_s00)))
        do = ("지표 <b>하나만</b> — <b>전환율</b>. 상세페이지 후반부(가격 제시·"
              "마무리 CTA)와 가격·구성을 봅니다. 소재는 건드리지 않습니다.")
    else:
        kind, head = "", "지금은 그대로 두고 지켜봅니다"
        why = ("광고 유입 상세 미도달 %s, 전환율 %s. "
               "비광고 대비 뚜렷하게 나쁘지 않습니다." % (G.pct(ad_s00), G.pct(cvr)))
        do = "바꾼 것이 있으면 그 효과가 나올 때까지 기다립니다. 동시에 여러 개를 손대지 않습니다."

    o = ['<div class="verdict %s"><h3>%s</h3>' % (kind, G.esc(head)),
         '<p class="why">%s</p>' % why,
         '<p class="do">다음에 할 것 — %s</p>' % do]
    o.append('<p class="hold">근거: 광고 집행 %d일 · 광고 유입 %s세션 · 주문 %d건. '
             'CTR·CPM·CPA 는 광고비와 노출 수가 필요해 여기서 판정하지 않습니다 '
             '(메타 광고관리자에서 확인).</p>'
             % (len(adays), f"{n_ses:,}", orders))
    o.append("</div>")
    return "".join(o)


def trend_block(pid, days):
    """날짜별 추이. 상세페이지를 고친 날 전후를 나란히 놓기 위한 표다.

    합산만 보면 어제 바꾼 것의 효과가 6일치 과거에 희석돼 안 보인다.
    변화를 판단하려면 '고친 날'에 선을 긋고 양쪽을 봐야 한다.

    ⚠️ 하루치는 표본이 얇다. 세션 30 미만인 날은 흐리게 그려서 그 줄만 보고
       판단하지 않게 한다 — 광고를 껐다 켠 날은 몇십 건밖에 안 들어온다.
    """
    rows = []
    for d in sorted(days):
        p = (days[d].get("products") or {}).get(pid)
        if not p:
            continue
        a = (p.get("devices") or {}).get("_all") or {}
        ses = a.get("sessions") or 0
        if not ses:
            continue
        sales = p.get("sales") or {}
        bc = p.get("by_channel") or {}
        tot_ch = sum(v.get("exits", 0) for v in bc.values())
        ad = sum(v.get("exits", 0) for k, v in bc.items() if k in PAID_CHANNELS)
        # 채널 수집(R10)은 2026-08-17 부터다. 그 전 날짜는 광고를 돌렸어도
        # 판별할 근거가 없다. 0% 로 그리면 '광고 안 한 날'로 읽혀 실제와 반대가 된다.
        rows.append({
            "date": d, "sessions": ses,
            "ad_share": (ad / tot_ch) if tot_ch else None,
            "s00": a.get("s00_rate") or 0,
            "done": a.get("done_rate") or 0,
            "orders": sales.get("orders") or 0,
            "net": sales.get("net") or 0,
        })
    if len(rows) < 3:
        return ""
    # 최근 14일만, 최신이 위. 어제 뭘 바꿨는지 확인하려고 여는 표라
    # 매번 맨 아래로 스크롤하게 두면 안 된다.
    rows = rows[-14:][::-1]
    mx = max(r["sessions"] for r in rows) or 1

    o = ["<h2>날짜별 추이</h2>",
         '<p class="sub">상세페이지나 광고를 바꾼 날 앞뒤를 나란히 보세요. '
         '<b>상세 미도달</b>은 이미지를 한 장도 못 보고 나간 비율입니다.</p>',
         '<div class="scroll"><table><tr><th>날짜</th>'
         '<th class="num">세션</th><th>규모</th>'
         '<th class="num">상세 미도달</th><th class="num">완독</th>'
         '<th class="num">주문</th><th class="num">매출</th></tr>']
    for r in rows:
        # 유료 비중 30% 이상이면 광고 집행일로 본다. 그 아래는 잔여 트래픽이다.
        is_ad = r["ad_share"] is not None and r["ad_share"] >= 0.3
        cls = " ".join(c for c in (("dim" if r["sessions"] < 30 else ""),
                                   ("adday" if is_ad else "")) if c)
        dim = ' class="%s"' % cls if cls else ""
        badge = G.sample_badge(r["sessions"])
        if is_ad:
            badge += '<span class="adtag">광고 %d%%</span>' % round(r["ad_share"] * 100)
        bar = G.reach_bar(r["sessions"] / mx, 0, w=90, h=11)
        o.append('<tr%s><td>%s%s</td><td class="num">%s</td><td class="bar">%s</td>'
                 '<td class="num">%s</td><td class="num">%s</td>'
                 '<td class="num">%s</td><td class="num">%s</td></tr>'
                 % (dim, r["date"][5:], badge, f'{r["sessions"]:,}', bar,
                    G.pct(r["s00"]), G.pct(r["done"]),
                    r["orders"] or "–",
                    ("%s원" % f'{r["net"]:,}') if r["net"] else "–"))
    o.append("</table></div>")
    return "".join(o)


def source_block(p):
    """유입별로 상세페이지를 어디까지 보는가.

    두 축을 나눠 그린다.
      채널(GA4 자동 분류) — 과거분까지 다 있어 표본이 두껍다. 판단의 정본.
      광고 소재(utm_content) — 파라미터를 붙인 뒤부터만. 소재 온오프 판단용.

    '상세 진입 전' 열을 따로 둔 이유: 상세영역에 들어서지도 못하고 나간 비율이
    유입 품질(광고 소재가 페이지와 안 맞음)을 가리키고, '평균 도달'은 들어선
    사람이 얼마나 읽었는지를 가리킨다. 둘을 섞으면 무엇을 고쳐야 할지 안 나온다.
    """
    total_sec = p.get("section_total") or 0
    ch = p.get("by_channel") or {}
    ads = {k: v for k, v in (p.get("by_source") or {}).items()
           if k != "_비광고" and v.get("exits", 0) > 0}
    if not ch and not ads:
        return ""

    o = ["<h2>어떻게 들어온 사람이 더 보나</h2>",
         '<p class="sub">유입 경로별로 상세페이지를 어디까지 보고 나갔는지입니다. '
         '<b>상세 진입 전</b>이 높으면 유입과 페이지가 안 맞는 것이고, '
         '<b>평균 도달</b>이 낮으면 페이지 자체 문제입니다.</p>']
    o.append(_depth_table(_depth_rows(ch, total_sec,
                                      lambda k: CHANNEL_KO.get(k, k)),
                          "유입 채널"))
    if ads:
        o.append('<h2 style="font-size:14px">광고 소재별</h2>')
        o.append(_depth_table(_depth_rows(ads, total_sec, lambda k: k), "광고 소재"))
    return "".join(o)


def channels_block(p):
    """유입 채널. '미도달 61%'를 해석하려면 누가 왔는지를 알아야 한다."""
    ch = p.get("channels") or {}
    if not ch:
        return ""
    total = sum(ch.values())
    rows = sorted(ch.items(), key=lambda kv: -kv[1])
    out = ["<h2>어디서 들어왔나</h2>",
           '<div class="scroll"><table><tr><th>유입 채널</th>'
           '<th class="num">세션</th><th class="num">비중</th></tr>']
    for name, n in rows:
        ko = CHANNEL_KO.get(name, name)
        dim = ' class="dim"' if name in ("Unassigned", "(미분류)") else ""
        out.append('<tr%s><td>%s</td><td class="num">%s</td>'
                   '<td class="num">%s</td></tr>'
                   % (dim, G.esc(ko), f"{n:,}", G.pct(n / total) if total else "–"))
    out.append("</table></div>")

    un = ch.get("Unassigned", 0) + ch.get("(미분류)", 0)
    if total and un / total > 0.3:
        # 원인을 구체적으로 적는다. "UTM이 없어서"만으로는 무엇을 고쳐야 할지 모른다.
        # 실측(2026-08-06): 미분류 세션에는 session_start 이벤트가 아예 없었다.
        out.append('<div class="note"><b>미분류가 %s입니다.</b> 이 세션들은 GA4에 '
                   '<b>세션 시작 기록 자체가 없습니다</b> — 유튜브·인스타 앱 안의 '
                   '브라우저로 들어오면 앱이 유입 출처를 지우기 때문입니다. '
                   '즉 유튜브 유입이 "영상(자연)"이 아니라 여기 섞여 있을 수 있습니다. '
                   '영상 설명란·프로필 링크에 UTM을 붙이면 갈라집니다'
                   '(저장소 <code>UTM_GUIDE.md</code>). '
                   '지금은 이 표로 채널별 성과를 판단할 수 없습니다.</div>'
                   % G.pct(un / total))
    return "".join(out)


def reading_block(p, date, days_collected):
    """관찰 메모. 처방이 아니라 '지금 숫자가 무엇을 말하는가'만 적는다.

    문장을 손으로 쓰지 않고 데이터에서 생성하는 이유: 손으로 쓰면 다음 날 숫자가
    바뀌어도 문장이 그대로 남아 거짓말이 된다.
    """
    a = p["devices"]["_all"]
    n = a["sessions"]
    if not n:
        return ""
    cl = p.get("clarity") or {}
    total = p.get("section_total") or 0
    notes = []

    # 이미지 무게를 먼저 말한다. 2026-08-07 Clarity 스크롤 실측에서 상단은 99%가
    # 통과하고 상세 이미지 초반에서 무너지는 것이 확인됐다 — "상단에서 붙잡힌다"는
    # 앞선 해석은 틀렸다. 원인 후보 1순위는 이미지 용량이다.
    sizes = p.get("image_bytes") or []
    heavy = [(i + 1, s) for i, s in enumerate(sizes) if s >= 1024 * 1024]
    total_mb = sum(sizes) / 1024 / 1024
    if heavy:
        notes.append("상세 이미지가 <b>총 %.1fMB</b>이고 그중 <b>%d장이 1MB를 넘습니다</b>"
                     "(가장 무거운 것 S%02d, %.1fMB). 모바일에서는 이미지가 뜨기 전에 "
                     "스크롤이 지나가버려, 사용자는 빈 화면을 넘기다 나가고 측정은 "
                     "'못 봤다'로 기록됩니다. <b>고칠 순서는 이미지 압축이 먼저입니다.</b>"
                     % (total_mb, len(heavy),
                        max(heavy, key=lambda x: x[1])[0],
                        max(s for _, s in heavy) / 1024 / 1024))

    if a["s00_rate"] >= 0.4:
        line = ("측정상 세션의 <b>%s</b>가 상세 이미지에 도달하지 못한 것으로 나옵니다"
                "(%d명). 다만 이 수치는 <b>이미지가 로드되지 않으면 도달로 세지 않는</b> "
                "방식이라, 실제로는 스크롤했는데 그림이 안 뜬 경우가 섞여 있습니다."
                % (G.pct(a["s00_rate"]), a["exit_s00"]))
        if a.get("avg_seconds"):
            line += " 평균 체류는 %d초입니다." % round(a["avg_seconds"])
        notes.append(line)

    ar = cl.get("active_ratio")
    if ar is not None and a.get("avg_seconds"):
        if ar >= 0.8:
            notes.append("활동 비율이 <b>%s</b>로 높습니다. 창만 켜두고 자리를 뜬 게 "
                         "아니라 실제로 페이지를 보고 있었다는 뜻이라, 위의 체류시간은 "
                         "실제 검토 시간으로 읽어도 됩니다." % G.pct(ar))
        elif ar < 0.5:
            notes.append("활동 비율이 <b>%s</b>로 낮습니다. 체류시간이 길어도 상당수는 "
                         "창을 켜둔 채 방치한 것이라 체류시간을 관심도로 읽으면 안 됩니다."
                         % G.pct(ar))

    if total > 1 and a.get("biggest_drop_section"):
        notes.append("구간 중에서는 <b>S%02d</b>의 낙차가 가장 큽니다(%s, %d명). "
                     "다만 S01 낙차는 3초 미만 이탈이 섞이므로 순위에서 제외했습니다."
                     % (a["biggest_drop_section"], G.pct(a["biggest_drop_rate"]),
                        a["biggest_drop_people"]))

    dead = cl.get("dead_click_sessions", 0)
    rage = cl.get("rage_click_sessions", 0)
    if cl.get("sessions"):
        if rage == 0 and dead <= max(2, cl["sessions"] * 0.05):
            notes.append("분노 클릭 %d건·데드 클릭 %d건으로 <b>조작 상의 막힘은 "
                         "관찰되지 않았습니다</b>. 이탈 원인을 버튼 오작동 쪽에서 찾을 "
                         "필요는 없어 보입니다." % (rage, dead))
        elif rage > 0:
            notes.append("분노 클릭이 <b>%d세션</b>에서 발생했습니다. 눌리지 않는 요소가 "
                         "있을 수 있으니 세션 리플레이로 확인할 값어치가 있습니다." % rage)

    if not notes:
        return ""

    head = ("<h2>지금 숫자가 말하는 것</h2>"
            '<p class="sub">아래는 관찰이지 처방이 아닙니다. '
            '%s</p>'
            % ("표본이 %d세션·수집 %d일차라 방향만 참고하세요."
               % (n, days_collected) if days_collected < ADVICE_MIN_DAYS
               else "수집 %d일차 기준입니다." % days_collected))
    return head + "".join('<div class="note">%s</div>' % t for t in notes)


def build_product(pid, p, date, days_collected, days=None):
    a = p["devices"]["_all"]
    n = a["sessions"]
    total = p["section_total"] or 0
    pcts, drops = a.get("section_reach_pct") or {}, a.get("section_dropoff") or {}
    reach = {int(k): v for k, v in (a.get("section_reach") or {}).items()}
    worst = a.get("biggest_drop_section")

    out = ["<h1>%s</h1>" % G.esc(p["name"]),
           '<p class="sub">%s번 · 이미지 %s장 · %s · %s 기준 · '
           '<a href="%s" target="_blank" rel="noopener">상세페이지 열기</a></p>'
           % (G.esc(pid), total or "–", G.esc(p["version"]), date, G.esc(p["url"]))]

    if n < MIN_SESSIONS:
        out.append('<div class="note">세션 <b>%d건</b>으로 표본이 부족합니다. '
                   '아래 비율은 참고용이며 판단 근거로 쓰기엔 이릅니다.</div>' % n)

    out.append('<div class="kpis">')
    out.append(G.kpi("세션", f"{n:,}", "pdp_exit 기준"))
    out.append(G.kpi("상세 미도달", G.frac(a["exit_s00"], n), "이미지를 못 봄"))
    out.append(G.kpi("첫 구간 이탈", G.frac(a["exit_s01"], n), "S01에서 나감"))
    out.append(G.kpi("끝까지 봄", G.frac(a["completed"], n), "S%02d 도달" % total if total else ""))
    if a.get("avg_seconds") is not None:
        out.append(G.kpi("평균 체류", "%d초" % round(a["avg_seconds"]), ""))
    s, dv = p.get("sales"), p.get("derived") or {}
    if s:
        out.append(G.kpi("순매출", "%s원" % f"{s['net']:,}",
                         "주문 %d건 · 세션당 %s원"
                         % (s["orders"], f"{dv.get('revenue_per_session', 0):,}")))
    cl = p.get("clarity")
    if cl and cl.get("active_ratio") is not None:
        # 체류시간만으로는 '보는 중'과 '켜두고 딴짓'이 구분되지 않는다.
        out.append(G.kpi("활동 비율", G.pct(cl["active_ratio"]),
                         "총 체류 중 실제로 움직인 시간"))
    if cl:
        # 분노/데드클릭은 '그 행동이 일어난 세션 수'다. 세션 수 자체가 아니다.
        out.append(G.kpi("Clarity 스크롤", "%s%%" % cl.get("avg_scroll_depth", "–"),
                         "세션 %s · 분노 %s · 데드 %s · 되돌아감 %s"
                         % (cl.get("sessions", "–"),
                            cl.get("rage_click_sessions", 0),
                            cl.get("dead_click_sessions", 0),
                            cl.get("quickback_sessions", 0))))
    out.append("</div>")

    if not total:
        out.append('<div class="note">구간 정보가 없어 이미지별 분석을 만들 수 없습니다.</div>')
        return G.page(p["name"], "".join(out), back=True)

    if total == 1:
        out.append('<div class="note">이 상품은 <b>상세 이미지가 1장</b>인 초장문 이미지라 '
                   '구간 분석이 성립하지 않습니다. 위의 Clarity 스크롤 비율로 보세요. '
                   '이미지를 여러 장으로 나누면 어디서 나가는지 볼 수 있습니다.</div>')
        return G.page(p["name"], "".join(out), back=True)

    body_at = len(out)
    out.append("<h2>구간별 도달과 이탈</h2>")
    out.append('<p class="sub">막대는 도달률, <span style="color:%s">붉은 조각</span>은 '
               '<b>그 구간을 마지막으로 세션이 끝난 사람</b>(pdp_exit 실측)입니다. '
               '도달률 계산 차이가 아니라 실제 이탈 지점입니다.</p>' % G.DROP)
    out.append('<div class="scroll"><table><tr>'
               '<th>구간</th><th>이미지</th><th>도달률</th>'
               '<th class="num">용량</th><th class="num">여기서 나감</th></tr>')
    sizes = p.get("image_bytes") or []

    # 막대와 숫자를 한 칸에 붙인다. 열을 나누면 숫자가 오른쪽 끝으로 밀려
    # 어느 막대의 값인지 눈이 왕복해야 한다.
    exit_hist = {int(k): v for k, v in (a.get("exit_hist") or {}).items()
                 if str(k).isdigit()}
    mono = {int(k): v for k, v in (a.get("section_reach_mono") or {}).items()}
    imgs = p.get("images") or []

    # 스크롤 도달 지점이 어느 구간에 떨어지는지 표시한다.
    # 우리 pdp_scroll 은 상세영역 기준이고 구간 위치도 상세영역 기준이라 바로 겹친다.
    # (Clarity 의 평균 스크롤은 **페이지 전체** 기준이라 좌표계가 달라 여기 못 얹는다.)
    spans = p.get("section_spans") or []
    curve_pct = a.get("scroll_curve_pct") or {}

    def section_of(detail_pos):
        for i, sp in enumerate(spans, 1):
            if sp[0] <= detail_pos < sp[1] or (i == len(spans) and detail_pos >= sp[0]):
                return i
        return None

    marks = {}
    for pctkey, share in sorted(curve_pct.items(), key=lambda kv: int(kv[0])):
        i = section_of(int(pctkey) / 100.0)
        if i:
            marks.setdefault(i, []).append(
                ("스크롤 %d%% 지점 · 여기까지 온 사람 %s" % (int(pctkey), G.pct(share)),
                 "ours"))

    # Clarity 평균을 상세영역 좌표로 옮긴다.
    #   상세영역_% = (페이지_% - 상세시작) / (상세끝 - 상세시작)
    # 실측한 레이아웃이 있는 상품만. 없으면 아예 안 그린다 — 좌표계가 다른 값을
    # 어림짐작으로 얹으면 조용히 틀린 그림이 된다.
    cl, lay = p.get("clarity") or {}, p.get("layout") or {}
    cl_note = None
    if cl.get("avg_scroll_depth") is not None and lay.get("detail_end_pct"):
        page = cl["avg_scroll_depth"] / 100.0
        st, en = lay["detail_start_pct"] / 100.0, lay["detail_end_pct"] / 100.0
        dpos = (page - st) / (en - st) if en > st else None
        if dpos is not None and 0 <= dpos <= 1:
            i = section_of(dpos)
            if i:
                marks.setdefault(i, []).append(
                    ("Clarity 평균 스크롤 %.1f%% 지점 (상세영역 %d%%)"
                     % (cl["avg_scroll_depth"], round(dpos * 100)), "clarity"))
        elif dpos is not None:
            cl_note = ("Clarity 평균 스크롤 %.1f%%는 상세 이미지 %s입니다."
                       % (cl["avg_scroll_depth"],
                          "시작 전" if dpos < 0 else "끝난 뒤"))
    for i in range(1, total + 1):
        rp = pcts.get(str(i), 0.0)
        left = exit_hist.get(i, 0)
        img = ('<img class="shot" loading="lazy" src="%s" alt="구간 %d">'
               % (G.esc(imgs[i - 1]), i) if i - 1 < len(imgs) else "")
        # 1MB 넘는 이미지는 모바일에서 뜨기 전에 지나쳐진다. 붉게 표시한다.
        kb = (sizes[i - 1] // 1024) if i - 1 < len(sizes) else 0
        if not kb:
            wt = '<span class="muted">–</span>'
        elif kb >= 1024:
            wt = '<b style="color:%s">%.1fMB</b>' % (G.DROP, kb / 1024)
        else:
            wt = "%dKB" % kb
        mk = "".join('<div class="mark %s">%s</div>' % (kind, G.esc(txt))
                     for txt, kind in marks.get(i, []))

        # 마지막 구간에서 끝난 세션은 '이탈'이 아니라 '완독'이다. 다 보고 나간 것과
        # 중간에 포기한 것은 성격이 정반대인데 같은 붉은색으로 그리면 오독된다.
        last = (i == total)
        if not left:
            leftcell = '<span class="muted">–</span>'
        elif last:
            leftcell = '<span class="done">%d명 완독</span>' % left
        else:
            leftcell = "%d명" % left
        out.append(
            '<tr class="%s"><td>S%02d%s</td><td>%s</td>'
            '<td class="bar">%s<span class="barval">%s</span>%s</td>'
            '<td class="num">%s</td><td class="num">%s</td></tr>'
            % ("hi" if i == worst else "", i,
               " ◀" if i == worst else "", img,
               G.reach_bar(rp, 0 if last else ((left / n) if n else 0)),
               G.frac(mono.get(i, 0), n), mk, wt, leftcell))
    out.append("</table></div>")

    if cl_note:
        out.append('<div class="note">%s 구간 표 안에는 표시하지 않습니다 — '
                   'Clarity는 페이지 전체 기준이고 이 표는 상세영역 기준이라 '
                   '범위를 벗어났습니다.</div>' % G.esc(cl_note))

    rep = a.get("reach_repaired_sections") or 0
    if rep:
        out.append('<div class="note"><b>도달률은 보정된 값입니다.</b> 상세 이미지가 '
                   '늦게 로드되면 그 구간이 "안 본 것"으로 빠지고, 자리를 차지하지 않아 '
                   '뒤 구간이 부풀어 곡선이 되레 올라갑니다. <b>S18을 봤다면 S14 자리는 '
                   '반드시 지나갔다</b>는 제약으로 %d개 구간을 메웠습니다. '
                   '보정 전 원본은 <code>data/pdp_daily.json</code>의 '
                   '<code>section_reach</code>에 그대로 있습니다.</div>' % rep)

    if worst and days_collected >= ADVICE_MIN_DAYS \
            and a.get("biggest_drop_people", 0) >= ADVICE_MIN_PEOPLE:
        out.append('<div class="note"><b>S%02d 이미지를 보세요.</b> 여기서 %s명이 '
                   '빠져나갑니다.</div>'
                   % (worst, f"{a['biggest_drop_people']:,}"))

    dev_at = len(out)
    out.append("<h2>기기별</h2>")
    out.append('<div class="scroll"><table><tr><th>기기</th><th class="num">세션</th>'
               '<th class="num">미도달</th><th class="num">첫 구간</th>'
               '<th class="num">완독</th></tr>')
    for dev, d in sorted(p["devices"].items()):
        if dev == "_all" or not d["sessions"]:
            continue
        out.append('<tr><td>%s</td><td class="num">%s</td><td class="num">%s</td>'
                   '<td class="num">%s</td><td class="num">%s</td></tr>'
                   % (G.esc(dev), f"{d['sessions']:,}", G.pct(d["s00_rate"]),
                      G.pct(d["s01_rate"]), G.pct(d["done_rate"])))
    out.append("</table></div>")

    devices_html = "".join(out[dev_at:])
    del out[dev_at:]

    # 탭으로 나눈다. 블록이 9개라 한 화면 스크롤로는 뒤쪽을 아무도 안 본다.
    # 순서는 '가장 자주 볼 것'부터 — 구간별 이탈이 이 대시보드의 존재 이유다.
    panes = [
        ("t1", "구간별 이탈", "".join(out[body_at:]) + scroll_block(p)),
        ("t2", "유입 분석", source_block(p) + channels_block(p)),
        ("t3", "날짜별 추이", verdict_block(pid, p, days or {}) + trend_block(pid, days or {})),
        ("t4", "행동·기기", entry_block(p) + zone_block(p) + devices_html),
        ("t5", "요약 해석", reading_block(p, date, days_collected)),
    ]
    panes = [(i, t, h) for i, t, h in panes if h.strip()]
    del out[body_at:]

    tab = ['<div class="tabs">']
    for k, (i, _t, _h) in enumerate(panes):
        tab.append('<input type="radio" name="tab" id="%s"%s>'
                   % (i, ' checked' if k == 0 else ''))
    tab.append('<div class="tabnav">')
    for i, t, _h in panes:
        tab.append('<label for="%s">%s</label>' % (i, G.esc(t)))
    tab.append("</div>")
    for i, _t, h in panes:
        tab.append('<div class="tabpane" id="p%s">%s</div>' % (i[1:], h))
    tab.append("</div>")
    out.append("".join(tab))

    out.append('<footer>도달률의 분모는 pdp_exit 건수(상세페이지를 연 세션)입니다. '
               'S00은 상세 이미지 영역에 도달하지 못한 세션으로, 첫 이미지 문제가 아니라 '
               '상단 영역이나 유입 소재 불일치를 뜻합니다.</footer>')
    return G.page(p["name"], "".join(out), back=True)


def main():
    data = C.load_json(SRC, {})
    days = data.get("days") or {}
    if not days:
        print("%s 가 비어 있다. merge_pdp.py 를 먼저 돌릴 것." % SRC, file=sys.stderr)
        return 1

    # 하루치가 아니라 기간 합산을 그린다.
    #
    # 예전엔 sorted(days)[-1] 로 '가장 최근 하루'만 썼는데, 수집이 07:20에 도니까
    # 화면에는 몇 시간치(8세션짜리 표)만 떴다. 며칠 모은 데이터가 통째로 안 보이고
    # 표본도 판단하기엔 너무 작았다.
    period = data.get("period") or {}
    rec = period if period.get("products") else days[sorted(days)[-1]]
    date = period.get("range") or sorted(days)[-1]

    # 상품이 하나도 안 잡힌 날은 세지 않는다. GA4 수집기가 최근 4일을 되받아오므로
    # 추적을 심기 전 날짜까지 들어오는데, 그걸 세면 게이트가 일찍 열려
    # 근거 없는 처방이 나간다.
    n_days = period.get("days_count") or sum(1 for d in days.values() if d.get("products"))
    os.makedirs(OUT, exist_ok=True)

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(date, rec, n_days))
    made = 1

    for pid, p in rec["products"].items():
        with open(os.path.join(OUT, "product-%s.html" % pid), "w",
                  encoding="utf-8") as f:
            f.write(build_product(pid, p, date, n_days, days))
        made += 1

    print("docs/ 에 %d개 페이지 생성 (기준일 %s, 수집 %d일차)" % (made, date, n_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
