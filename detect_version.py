"""상세페이지 버전 자동 감지.

공개 PDP HTML을 크롤해 상세 이미지 목록의 해시로 버전을 판정한다.
GA4의 pdp_section_total은 세션마다 흔들리므로(레이지 이미지 로드 실패) 판정 근거로 쓰지 않는다.

오탐 완화 4단:
  ① 정규화      — CDN 경로·쿼리·카페24 복제 접두어(copy-<epoch>-) 제거
  ② 공용 배너    — 전체 상품의 50% 이상에 등장하는 이미지는 해시에서 제외
  ③ 변경 등급    — major(개수 변경/20%↑/앞3장) · minor · none
  ④ 디바운스     — 연속 2회 동일해야 확정. 0장 응답은 수집 실패로 폐기

사용:
  python detect_version.py            # 크롤 → data/pdp_versions.json 갱신
  python detect_version.py --dry-run  # 저장 없이 결과만 출력
"""
import argparse
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request

from pdp_common import (MALL_HOST, UA, kst_now, kst_today, load_json,
                        http_get_text, save_json)

PRODUCTS_PATH = "data/pdp_products.json"
VERSIONS_PATH = "data/pdp_versions.json"
OVERRIDES_PATH = "data/pdp_version_overrides.json"

BANNER_SHARE = 0.5      # 이 비율 이상 상품에 등장하면 공용 배너
MAJOR_RATIO = 0.20      # 20% 이상 교체면 major
HEAD_N = 3              # 앞 3장은 교체만으로도 major
CONFIRM_SEEN = 2        # 연속 2회 관측되어야 확정

_RE_LIST_ITEM = re.compile(r'/product/([^"\'/]+)/(\d+)/category/')
_RE_DETAIL_BLOCK = re.compile(
    r'id=["\']prdDetail["\'](.*?)(?:id=["\']prd-review|id=["\']prdReview|</body)',
    re.S)
_RE_IMG_SRC = re.compile(r'(?:ec-data-src|data-src|src)=["\']([^"\']+)["\']')
_RE_COPY_PREFIX = re.compile(r'copy-\d{9,}-')


# ---------- 상품 목록 ----------

def fetch_product_list():
    """전체상품 카테고리에서 상품번호·슬러그를 수집한다.

    GA4 R4가 붙기 전까지의 부트스트랩 경로. 카페24 상품 스코프가 필요 없다.
    """
    html = http_get_text(f"{MALL_HOST}/category/%EC%A0%84%EC%B2%B4%EC%83%81%ED%92%88/54/")
    found = {}
    for slug, pno in _RE_LIST_ITEM.findall(html):
        found.setdefault(pno, {"slug": slug})
    return found


# ---------- 상세 이미지 ----------

_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# 상품명 후보에서 걷어낼 것들. 카페24 <title> 은 "큐라엘 | 상품명" 또는
# "상품명 | 큐라엘몰" 처럼 몰 이름이 붙는다.
_TITLE_NOISE = ("큐라엘몰", "큐라엘", "CURAEL", "curael")


def title_to_name(html):
    """<title> 에서 상품명만 남긴다. 못 뽑으면 빈 문자열.

    주문이 한 번도 없는 상품은 카페24 이름이 없고, GA4 이름은 추적 스크립트가
    상품명 요소를 못 잡으면 몰 이름("큐라엘")을 집어온다. 그때 쓸 마지막 근거다.
    """
    m = _RE_TITLE.search(html or "")
    if not m:
        return ""
    raw = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    parts = [x.strip() for x in raw.split("|") if x.strip()]
    for x in parts:
        if x not in _TITLE_NOISE:
            return x
    return ""


def crawl_detail_images(pno):
    """#prdDetail 블록 안의 이미지 src를 DOM 순서대로. 실패 시 빈 리스트.

    (imgs, name) 을 돌려준다. 이름은 <title> 에서 뽑은 상품명이다.
    """
    url = f"{MALL_HOST}/product/detail.html?product_no={pno}"
    try:
        html = http_get_text(url)
    except Exception as e:
        print(f"  [{pno}] 크롤 실패: {e}", file=sys.stderr)
        return [], ""
    name = title_to_name(html)
    m = _RE_DETAIL_BLOCK.search(html)
    if not m:
        print(f"  [{pno}] prdDetail 블록 없음", file=sys.stderr)
        return [], name
    return _RE_IMG_SRC.findall(m.group(1)), name


def absolutize(src):
    """표시용 절대 URL. 정규화와 목적이 완전히 다르므로 절대 섞지 말 것.

    normalize_one() 은 '같은 이미지인가'를 판정하려고 호스트·copy-<epoch>- 를 지운다.
    그 결과물로는 URL을 복원할 수 없다(실제로 404가 났다). 화면에 그림을 띄우려면
    크롤한 원본 src 를 그대로 살려둬야 한다.
    """
    s = (src or "").strip()
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return MALL_HOST + s
    return s


def fetch_image_sizes(urls, workers=8):
    """구간별 이미지 바이트 수. HEAD 요청이라 본문은 안 받는다.

    실패하면 0 을 넣는다 — 0 과 '작다'는 다르므로 화면에서 구분해야 한다.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(u):
        try:
            req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return int(r.headers.get("content-length") or 0)
        except Exception:
            return 0

    if not urls:
        return []
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, urls))


def _img_size(url):
    """이미지 픽셀 크기를 헤더만 읽어서 알아낸다. 본문 전체를 받지 않는다.

    구간이 상세영역의 몇 % 지점인지 계산하려면 각 이미지의 세로 길이가 필요하다.
    상세 이미지는 전부 컨테이너 폭에 맞춰 늘어나므로 **화면에서의 높이 비율은
    height/width 에만 좌우된다** — 기기 폭과 무관해서 한 번 재두면 어디서나 쓸 수 있다.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-4095"})
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read(4096)
    except Exception:
        return None

    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big"))
        if b[:3] == b"GIF":
            return (int.from_bytes(b[6:8], "little"), int.from_bytes(b[8:10], "little"))
        if b[:2] == b"\xff\xd8":                      # JPEG — SOFn 마커를 찾는다
            i = 2
            while i < len(b) - 9:
                if b[i] != 0xFF:
                    i += 1
                    continue
                m = b[i + 1]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    return (int.from_bytes(b[i + 7:i + 9], "big"),
                            int.from_bytes(b[i + 5:i + 7], "big"))
                i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    except Exception:
        pass
    return None


def fetch_image_dims(urls, workers=8):
    from concurrent.futures import ThreadPoolExecutor
    if not urls:
        return []
    with ThreadPoolExecutor(workers) as ex:
        return [list(d) if d else None for d in ex.map(_img_size, urls)]


def normalize_one(src):
    """비교용 이름으로 정규화.

    - 쿼리스트링 제거 (캐시버스터)
    - CDN 호스트/몰 경로 접두어 제거 (경로가 회전할 수 있다)
    - 카페24 복제 접두어 copy-<epoch>- 제거  ← 이걸 안 벗기면 재업로드마다 오탐
    - 업로드 날짜 디렉터리(/20250721/)는 보존 — 같은 파일명이 다른 날짜면 실제 교체
    """
    s = src.split("?")[0]
    s = re.sub(r"^https?:", "", s)
    s = re.sub(r"^//[^/]+/", "", s)              # 호스트 제거
    s = re.sub(r"^pg[0-9a-z]+/[^/]+/", "", s)    # pg…/yulbangjyh/ 제거
    s = re.sub(r"^(?:web/)?upload/", "", s)      # web/upload/ 제거
    s = _RE_COPY_PREFIX.sub("", s)
    return s


def detect_shared_banners(per_product, threshold=BANNER_SHARE):
    """공용 배너 판정 — 둘 중 하나면 배너로 보고 해시에서 제외한다.

    (A) 전체 상품의 threshold 이상에 등장  → 전사 공지성 이미지
    (B) 2개 이상 상품에 등장하면서 **등장하는 모든 상품에서 맨 앞(index 0)**
        → 상단 고정 배너. 실측상 notice_banner가 주력 2개 상품에만 걸려 있어
          (A)만으로는 못 걸러진다. 위치 신호가 이름 휴리스틱보다 정확하다.

    배너를 제외하지 않으면 배너 교체만으로 상품 버전이 올라가고
    구간 번호가 통째로 밀려 이전 버전과 비교가 불가능해진다.
    """
    n = len([v for v in per_product.values() if v])
    if n == 0:
        return set()

    counts, positions = {}, {}
    for names in per_product.values():
        if not names:
            continue
        for i, nm in enumerate(names):
            if nm in positions.setdefault(nm, set()) or True:
                positions[nm].add(i)
        for nm in set(names):
            counts[nm] = counts.get(nm, 0) + 1

    banners = set()
    for nm, c in counts.items():
        if c >= max(2, n * threshold):
            banners.add(nm)
        elif c >= 2 and positions.get(nm) == {0}:
            banners.add(nm)
    return banners


def _hash(names):
    return hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()[:12]


def classify_change(old, new):
    """변경 등급 판정. old/new는 배너를 제외한 정규화 이름 리스트."""
    if old == new:
        return {"level": "none", "reason": "identical"}
    if not old:
        return {"level": "major", "reason": "initial"}

    if len(old) != len(new):
        return {"level": "major",
                "reason": f"image_count {len(old)}→{len(new)}",
                "diff": _diff(old, new)}

    changed = [i for i in range(len(new)) if old[i] != new[i]]
    head_changed = [i for i in changed if i < HEAD_N]
    ratio = len(changed) / max(1, len(new))

    if head_changed:
        return {"level": "major",
                "reason": f"앞 {HEAD_N}장 중 교체 (구간 {[i+1 for i in head_changed]})",
                "diff": _diff(old, new)}
    if ratio >= MAJOR_RATIO:
        return {"level": "major",
                "reason": f"{len(changed)}/{len(new)}장 교체 ({ratio:.0%})",
                "diff": _diff(old, new)}
    return {"level": "minor",
            "reason": f"{len(changed)}장 교체 (구간 {[i+1 for i in changed]})",
            "diff": _diff(old, new)}


def _diff(old, new):
    so, sn = set(old), set(new)
    return {"added": sorted(sn - so), "removed": sorted(so - sn)}


# ---------- 본체 ----------

def detect(dry_run=False, sleep=1.0):
    today = kst_today()
    products = load_json(PRODUCTS_PATH)
    store = load_json(VERSIONS_PATH)
    overrides = load_json(OVERRIDES_PATH)

    listing = fetch_product_list()
    # 전체상품 카테고리에 안 걸리는 상품이 있다(실측: 60·61·63 유기농 먹거리).
    # 트래픽은 들어오는데 크롤을 안 하니 이름도 이미지도 비어 화면에 "큐라엘"로
    # 떴다. GA4 가 본 적 있는 상품번호를 합쳐서, 사람이 보는 상품은 다 크롤한다.
    seen = set()
    try:
        ga4 = load_json("data/ga4_pdp_history.json") or {}
        for rec in (ga4.get("days") or {}).values():
            seen.update(k for k in (rec.get("products") or {}) if k.isdigit())
    except Exception as e:
        print(f"  GA4 상품 목록을 못 읽었습니다({e}). 카테고리 목록만 씁니다.",
              file=sys.stderr)
    extra = sorted(seen - set(listing), key=int)
    for pno in extra:
        listing[pno] = {"slug": (products.get(pno) or {}).get("slug", "")}
    if extra:
        print(f"카테고리 밖 상품 {len(extra)}개 추가: {', '.join(extra)}")
    print(f"상품 {len(listing)}개 발견")
    for pno, meta in listing.items():
        products.setdefault(pno, {})
        products[pno].setdefault("slug", meta["slug"])
        products[pno]["last_seen"] = today
        products[pno].setdefault("first_seen", today)

    raw, display, titles = {}, {}, {}
    for pno in sorted(listing, key=int):
        srcs, tname = crawl_detail_images(pno)
        if tname:
            titles[pno] = tname
        raw[pno] = [normalize_one(s) for s in srcs]
        # 표시용은 **배너를 포함한 DOM 순서 그대로**여야 한다. 추적 스크립트는
        # #prdDetail 의 <img> 를 전부 세므로, 배너를 뺀 목록으로 그림을 붙이면
        # 구간 번호가 한 칸씩 밀려 매 구간마다 엉뚱한 이미지를 보여주게 된다.
        # 버전 판정용(배너 제외)과 표시용(배너 포함)은 서로 다른 목록이다.
        display[pno] = [absolutize(s) for s in srcs]
        print(f"  [{pno}] 이미지 {len(raw[pno])}장")
        time.sleep(sleep)

    banners = detect_shared_banners(raw)
    if banners:
        print(f"공용 배너 {len(banners)}개 제외: {sorted(banners)}")

    changes = []
    for pno, names in raw.items():
        if not names:
            # 0장 = 수집 실패로 간주. 기존 버전을 절대 건드리지 않는다.
            print(f"  [{pno}] 0장 → 수집 실패로 폐기 (기존 버전 유지)")
            continue

        body = [n for n in names if n not in banners]
        h = _hash(body)
        entry = store.get(pno) or {}
        cur = entry.get("current")

        # 표시용 URL은 버전의 정체성이 아니라 화면 재료다. 버전이 안 바뀌어도
        # 매 크롤마다 최신으로 덮어쓴다(current 안에 넣으면 버전 고정 시 낡는다).
        entry["display_urls"] = display.get(pno) or []
        # 몰이 표시하는 상품명. 주문이 없어 카페24 이름이 없고 GA4 이름도
        # 못 믿을 때 병합이 마지막으로 집는 값이다.
        if titles.get(pno):
            entry["title_name"] = titles[pno]

        # 구간별 이미지 용량. 이게 이탈의 유력한 원인이라 매일 추적한다.
        #
        # 2026-08-07 실측: 26번 상세 이미지 20장이 25.4MB, 한 장이 4.8MB였다.
        # Clarity 스크롤로 보면 상단은 99%가 통과하는데 S02~S05 에서 90%→48% 로
        # 무너지는데, 그 구간이 무거운 이미지가 시작되는 지점과 정확히 겹친다.
        # 압축 전후를 비교하려면 용량이 데이터로 남아 있어야 한다.
        entry["image_bytes"] = fetch_image_sizes(display.get(pno) or [])
        entry["image_dims"] = fetch_image_dims(display.get(pno) or [])

        if cur and cur.get("struct_hash") == h:
            entry.pop("pending", None)
            store[pno] = entry
            continue

        pend = entry.get("pending") or {}
        if pend.get("struct_hash") == h:
            pend["seen_count"] = pend.get("seen_count", 1) + 1
        else:
            pend = {"struct_hash": h, "images": body, "first_seen": today,
                    "seen_count": 1}

        if pend["seen_count"] < CONFIRM_SEEN:
            entry["pending"] = pend
            store[pno] = entry
            print(f"  [{pno}] 변경 감지 (pending {pend['seen_count']}/{CONFIRM_SEEN})")
            continue

        # 확정
        old_names = (cur or {}).get("images", [])
        cls = classify_change(old_names, body)
        if cls["level"] == "none":
            entry.pop("pending", None)
            store[pno] = entry
            continue

        ver = (cur or {}).get("version", 0)
        minor = (cur or {}).get("minor", 0)
        if cls["level"] == "major":
            ver, minor = ver + 1, 0
        else:
            minor += 1

        ov = (overrides.get(pno) or [])
        for o in ov:
            if o.get("date") == today and o.get("version"):
                ver, minor = o["version"], 0
                cls["reason"] += f" (수동 오버라이드: {o.get('note','')})"

        hist = entry.get("history") or []
        if cur:
            cur = dict(cur)
            cur["to"] = today
            hist.append(cur)
        entry["history"] = hist
        entry["current"] = {"version": ver, "minor": minor, "since": today,
                            "struct_hash": h, "image_count": len(body),
                            "images": body, "reason": cls["reason"],
                            "diff": cls.get("diff", {})}
        entry.pop("pending", None)
        store[pno] = entry
        changes.append((pno, f"v{ver}.{minor}", cls["level"], cls["reason"]))
        print(f"  [{pno}] ★ v{ver}.{minor} 확정 — {cls['level']}: {cls['reason']}")

    store["_crawled_at"] = kst_now().isoformat(timespec="seconds")
    store["_shared_banners"] = sorted(banners)

    if dry_run:
        print("\n[dry-run] 저장하지 않음")
    else:
        save_json(PRODUCTS_PATH, products)
        save_json(VERSIONS_PATH, store)
        print(f"\n저장: {PRODUCTS_PATH}, {VERSIONS_PATH}")

    if changes:
        print("\n확정된 버전 변경:")
        for c in changes:
            print("  ", c)
    return store


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()
    detect(dry_run=a.dry_run, sleep=a.sleep)
