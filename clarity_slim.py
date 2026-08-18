"""Clarity 원본 응답을 저장소에 올릴 수 있는 크기로 접는다.

왜 필요한가
-----------
원본 `data/clarity_snapshots.json` 이 2026-08-14 에 GitHub 의 **파일당 100 MiB
한도**를 넘었다. 그 뒤로 clarity daily 워크플로의 push 가 매번 거부됐고(5회 재시도가
전부 동일하게 실패), 수집분이 저장소에 남지 않으니 다음 실행이 수집 시각·예산을
알 수 없어 또 호출 → 하루 10회 한도 초과 → 429. 그렇게 5일치가 날아갔다.

무엇을 버리는가
---------------
접는 기준은 URL 이 아니라 **상품번호**다. merge_pdp 가 어차피 상품번호로 묶고,
상품에 안 붙는 URL 은 '미매칭 세션 수' 합계로만 쓰기 때문이다. 그래서 지표당
`상품 수 + 1(_other)` 행만 남으면 화면에 나오는 숫자가 전부 보존된다.

URL 을 그대로 두면 안 되는 이유는 쿼리스트링이다. 유튜브 유입이 붙이는
`yts_source=...` 하나가 수백 바이트고, 같은 페이지가 파라미터만 다른 수백 개 행으로
쪼개져 들어온다. 지표는 9종 × 최대 1000행이라 상한이 있는데 URL 만 무한정 길어졌다.

⚠ 접고 나면 URL 단위 재분석은 못 한다. 원본은 로컬 PC 에만 남는다(.gitignore).
   Actions 가 수집한 날은 요약본만 존재한다.

집계 규칙 — merge_pdp.merge_clarity 의 산식을 그대로 보존한다
------------------------------------------------------------
- 행동지표는 merge 가 `sessionsCount × 비율/100` 으로 쓴다. 비율을 단순 평균하면
  값이 바뀌므로 세션 수로 가중해 더한 뒤 마지막에 나눈다.
- Traffic 은 merge 가 행마다 `totalSessionCount or distinctUserCount` 로 읽는다.
  두 필드를 따로 합치면 0인 행의 폴백이 사라져 세션이 증발한다(실측 59세션).
  그래서 **행 단위로 폴백을 적용한 값**을 합쳐 totalSessionCount 에 넣는다.
- ScrollDepth 는 Traffic 의 세션 수를 가중치로 쓴다. 방문 1건짜리 URL 과
  1000건짜리를 같은 무게로 평균하면 안 된다.
"""
import pdp_common as C

BEHAVIOR = ("DeadClickCount", "ExcessiveScroll", "RageClickCount",
            "QuickbackClick", "ScriptErrorCount", "ErrorClickCount")

OTHER = "_other"
_BASE = "https://curaelmall.com"


def _key(url):
    return C.parse_product_no(url or "") or OTHER


def _url_for(key):
    """merge 가 다시 상품번호를 뽑아낼 수 있는 최소한의 URL 을 만든다."""
    if key == OTHER:
        return _BASE + "/_other"
    return _BASE + "/product/detail.html?product_no=" + key


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(x):
    return int(_num(x))


def _weights(traffic_rows):
    """URL별 세션 수. merge 와 같은 폴백 규칙을 쓴다."""
    w = {}
    for r in traffic_rows:
        u = r.get("Url")
        w[u] = w.get(u, 0) + (_int(r.get("totalSessionCount"))
                              or _int(r.get("distinctUserCount")))
    return w


def slim_metrics(metrics, extra_dims=()):
    """extra_dims: URL 과 함께 조회한 나머지 차원(예: Device). 그 값은 보존한다."""
    out = {}
    weights = _weights((metrics or {}).get("Traffic") or [])

    for name, rows in (metrics or {}).items():
        acc = {}
        for r in rows:
            u = r.get("Url")
            gk = (_key(u),) + tuple(r.get(d) for d in extra_dims)
            a = acc.setdefault(gk, {})

            if name in BEHAVIOR:
                n = _int(r.get("sessionsCount"))
                a["sessionsCount"] = a.get("sessionsCount", 0) + n
                a["_hit"] = a.get("_hit", 0.0) + n * _num(
                    r.get("sessionsWithMetricPercentage")) / 100.0
                a["subTotal"] = a.get("subTotal", 0) + _int(r.get("subTotal"))
                a["pagesViews"] = a.get("pagesViews", 0) + _int(r.get("pagesViews"))
            elif name == "ScrollDepth":
                w = max(1, weights.get(u, 1))
                a["_num"] = a.get("_num", 0.0) + _num(r.get("averageScrollDepth")) * w
                a["_den"] = a.get("_den", 0) + w
            elif name == "Traffic":
                a["totalSessionCount"] = a.get("totalSessionCount", 0) + (
                    _int(r.get("totalSessionCount")) or _int(r.get("distinctUserCount")))
                a["distinctUserCount"] = (a.get("distinctUserCount", 0)
                                          + _int(r.get("distinctUserCount")))
                a["totalBotSessionCount"] = (a.get("totalBotSessionCount", 0)
                                             + _int(r.get("totalBotSessionCount")))
            elif name == "EngagementTime":
                for f in ("totalTime", "activeTime"):
                    a[f] = a.get(f, 0) + _int(r.get(f))
            else:
                # 모르는 지표는 숫자 필드만 더해 둔다 — 조용히 버리지 않는다
                for f, v in r.items():
                    if f != "Url":
                        a[f] = a.get(f, 0) + _num(v)

        rows_out = []
        for gk, a in acc.items():
            k = gk[0]
            if name in BEHAVIOR:
                n = a.get("sessionsCount", 0)
                a["sessionsWithMetricPercentage"] = (
                    round(100.0 * a.pop("_hit", 0.0) / n, 9) if n else 0)
            elif name == "ScrollDepth":
                den = a.pop("_den", 0)
                a["averageScrollDepth"] = (
                    round(a.pop("_num", 0.0) / den, 9) if den else 0)
                a.pop("_num", None)
            a["Url"] = _url_for(k)
            for d, v in zip(extra_dims, gk[1:]):
                a[d] = v
            rows_out.append(a)
        out[name] = rows_out
    return out


def slim_entry(entry):
    """스냅샷 1건을 접는다. 메타데이터는 그대로 둔다(용량을 안 먹는다)."""
    out = {k: v for k, v in entry.items() if k != "calls"}
    calls = {}
    for name, call in (entry.get("calls") or {}).items():
        c = {k: v for k, v in call.items() if k != "metrics"}
        m = call.get("metrics") or {}
        dims = list(call.get("dims") or ([name] if name == "URL" else []))
        # URL 이 낀 조합만 크다(URL|Device 가 44.8MB 였다). 나머지는 수백 행이라 둔다.
        has_url = "URL" in dims
        extra = tuple(d for d in dims if d != "URL")
        c["metrics"] = slim_metrics(m, extra) if has_url else m
        c["slimmed"] = has_url
        calls[name] = c
    out["calls"] = calls
    return out


def slim_all(snapshots):
    return {"snapshots": {k: slim_entry(v)
                          for k, v in (snapshots.get("snapshots") or {}).items()}}
