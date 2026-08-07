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
           '<p class="sub">%s 기준 · 수집 %d일차 · 생성 %s</p>'
           % (date, days_collected, C.kst_now().strftime("%m-%d %H:%M"))]

    if days_collected < ADVICE_MIN_DAYS:
        out.append(
            '<div class="note"><b>수집 %d일차입니다.</b> 방향만 참고하세요. '
            '표본이 쌓이기 전의 숫자로 상세페이지를 고치면 엉뚱한 곳을 고치게 됩니다. '
            '처방은 7일치가 모이면 표시됩니다.</div>' % days_collected)

    if rec["sources"]["cafe24"] != "ok":
        out.append('<div class="note">매출 데이터 <b>미수집</b> — 이 날짜는 카페24 수집이 '
                   '돌지 않았습니다. 매출 칸의 회색 <b>–</b>는 0원이 아니라 모른다는 뜻입니다.</div>')

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


def build_product(pid, p, date, days_collected):
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
        out.append(
            '<tr class="%s"><td>S%02d%s</td><td>%s</td>'
            '<td class="bar">%s<span class="barval">%s</span></td>'
            '<td class="num">%s</td><td class="num">%s</td></tr>'
            % ("hi" if i == worst else "", i,
               " ◀" if i == worst else "", img,
               G.reach_bar(rp, (left / n) if n else 0),
               G.frac(mono.get(i, 0), n), wt,
               ("%d명" % left) if left else '<span class="muted">–</span>'))
    out.append("</table></div>")

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

    out.append(entry_block(p))
    out.append(zone_block(p))
    out.append(scroll_block(p))
    out.append(channels_block(p))
    out.append(reading_block(p, date, days_collected))

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

    date = sorted(days)[-1]
    rec = days[date]

    # 상품이 하나도 안 잡힌 날은 세지 않는다. GA4 수집기가 최근 4일을 되받아오므로
    # 추적을 심기 전 날짜까지 들어오는데, 그걸 세면 "수집 4일차"가 되어
    # 7일 게이트가 일찍 열리고 근거 없는 처방이 나간다.
    n_days = sum(1 for d in days.values() if d.get("products"))
    os.makedirs(OUT, exist_ok=True)

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(date, rec, n_days))
    made = 1

    for pid, p in rec["products"].items():
        with open(os.path.join(OUT, "product-%s.html" % pid), "w",
                  encoding="utf-8") as f:
            f.write(build_product(pid, p, date, n_days))
        made += 1

    print("docs/ 에 %d개 페이지 생성 (기준일 %s, 수집 %d일차)" % (made, date, n_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
