"""차트 조각. 순수 함수만 두고 파일은 읽지 않는다.

데이터 접근이 섞이면 차트를 단독으로 시험할 수 없다. 여기 있는 함수는 전부
숫자를 받아 문자열을 돌려주기만 한다.

외부 JS·CDN 금지(vegicel-ad-autopilot 관례). 인라인 SVG와 순수 HTML만 쓴다.
"""

BAR = "#15776A"       # 도달
DROP = "#C0392B"      # 낙차 — '여기가 문제'라는 한 가지 의미에만 쓴다
MUTED = "#9AA5A3"
HILITE = "#FDF3E7"    # 최대 낙차 행


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def pct(x):
    return "%.0f%%" % (x * 100)


def frac(n, d):
    """분모를 항상 병기한다. 예외 없음 — 62% 만 보여주면 27명짜리 62%를 못 걸러낸다."""
    return "%s (%s/%s)" % (pct(n / d) if d else "–", f"{n:,}", f"{d:,}")


def sparkline(values, w=104, h=26):
    """상품 목록용 미니 도달 곡선.

    20개 구간을 숫자 열로 늘어놓으면 13개 상품을 비교할 수 없다. 모양이 훨씬 빠르다.
    """
    if not values:
        return '<span style="color:%s">–</span>' % MUTED
    n = len(values)
    if n == 1:
        values = values * 2
        n = 2
    step = w / (n - 1)
    pts = " ".join("%.1f,%.1f" % (i * step, h - 1 - v * (h - 2))
                   for i, v in enumerate(values))
    area = "0,%s " % (h - 1) + pts + " %s,%s" % (w, h - 1)
    return (
        '<svg width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
        'aria-label="구간 도달 곡선">'
        '<polygon points="%s" fill="%s" opacity=".14"/>'
        '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>'
        '</svg>' % (w, h, w, h, area, BAR, pts, BAR))


def reach_bar(reach_pct, drop_rate, w=210, h=15):
    """도달 막대 + 같은 막대 안의 붉은 낙차 조각.

    낙차를 숫자 열이 아니라 '면적'으로 보여주는 게 이 화면의 핵심이다.
    표를 안 읽고 붉은 조각 크기만 훑어도 어디가 문제인지 나온다.

    도달률에 따라 색을 3~5단계로 바꾸지 않는다 — 길이가 이미 같은 정보를 담고 있어
    중복이고, 보는 사람에게 범례 해석을 요구하게 된다.
    """
    fill = max(0.0, min(1.0, reach_pct)) * w
    d = max(0.0, min(1.0, drop_rate or 0)) * w
    return (
        '<svg width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
        'aria-label="도달 %s">'
        '<rect width="%d" height="%d" rx="2" fill="#E8EDEC"/>'
        '<rect width="%.1f" height="%d" rx="2" fill="%s"/>'
        '<rect x="%.1f" width="%.1f" height="%d" fill="%s" opacity=".55"/>'
        '</svg>' % (w, h, w, h, pct(reach_pct), w, h, fill, h, BAR,
                    fill, d, h, DROP))


def kpi(label, value, sub=""):
    return (
        '<div class="kpi"><div class="kpi-v">%s</div>'
        '<div class="kpi-l">%s</div>'
        '<div class="kpi-s">%s</div></div>' % (esc(value), esc(label), esc(sub)))


def sample_badge(sessions, minimum=30):
    if sessions >= 100:
        return ""
    if sessions >= minimum:
        return '<span class="badge">참고</span>'
    return '<span class="badge low">표본 부족</span>'


CSS = """
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
 "Malgun Gothic",sans-serif;color:#1D2624;background:#F6F8F8}
.wrap{max-width:1040px;margin:0 auto;padding:22px 16px 72px}
h1{font-size:21px;margin:0 0 2px}
h2{font-size:16px;margin:34px 0 10px}
.sub{color:#6B7876;font-size:13px;margin:0 0 18px}
.note{background:#FFF6E5;border:1px solid #F0DCB4;border-radius:8px;
 padding:11px 13px;font-size:13px;color:#6B5A34;margin:0 0 20px}
.note b{color:#4A3D22}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:#fff;border:1px solid #E3E9E8;border-radius:9px;padding:13px 14px}
.kpi-v{font-size:23px;font-weight:650;letter-spacing:-.01em}
.kpi-l{font-size:12px;color:#6B7876;margin-top:3px}
.kpi-s{font-size:11px;color:#9AA5A3;margin-top:1px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;
 border:1px solid #E3E9E8;border-radius:9px;overflow:hidden}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #EEF2F1;
 white-space:nowrap;vertical-align:middle}
th{font-size:11px;color:#6B7876;font-weight:600;background:#FBFCFC}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
/* 막대와 값을 한 칸에 나란히 — 열을 나누면 숫자가 오른쪽 끝으로 밀려
   어느 막대의 값인지 확인하려고 눈이 좌우로 왕복하게 된다. */
td.bar{white-space:nowrap}
td.bar svg{vertical-align:middle}
.barval{margin-left:9px;font-variant-numeric:tabular-nums;color:#3D4C49}
/* 스크롤 도달 지점 표시. 구간 위치와 스크롤 %가 같은 좌표계(상세영역 기준)라
   바로 겹쳐 놓을 수 있다. Clarity 평균은 페이지 전체 기준이라 여기 못 얹는다. */
.mark{margin-top:5px;font-size:11px;color:#8A6D3B;background:#FCF3E3;
 border-left:3px solid #E0A94A;padding:2px 7px;border-radius:0 3px 3px 0;
 display:block;white-space:normal;max-width:210px;line-height:1.35}
tr:last-child td{border-bottom:0}
a{color:#15776A;text-decoration:none}
a:hover{text-decoration:underline}
.thumb{width:34px;height:34px;object-fit:cover;border-radius:5px;
 border:1px solid #E3E9E8;background:#F1F4F4;display:block}
.shot{width:74px;border-radius:5px;border:1px solid #E3E9E8;display:block;
 background:#F1F4F4}
.badge{font-size:10px;background:#EEF2F1;color:#6B7876;border-radius:4px;
 padding:1px 5px;margin-left:5px}
.badge.low{background:#F4E9E7;color:#9C5A4E}
.dim{opacity:.55}
.hi{background:#FDF3E7}
.muted{color:#9AA5A3}
footer{margin-top:34px;font-size:12px;color:#9AA5A3}
"""


def page(title, body, back=None):
    nav = ('<p class="sub"><a href="index.html">&larr; 전체 목록</a></p>'
           if back else "")
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        '<title>%s</title><style>%s</style></head><body><div class="wrap">%s%s'
        '</div></body></html>' % (esc(title), CSS, nav, body))
