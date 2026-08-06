"""큐라엘몰 PDP 파이프라인 공용 유틸.

vegicel-ad-autopilot 관례를 따른다 — 표준 라이브러리만 쓰고, data/*.json에 적재한다.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Windows 콘솔 기본 코드페이지(cp949)는 '—' 같은 문자에서 UnicodeEncodeError로 죽는다.
# 로컬 배치로 돌리는 스크립트라 출력 때문에 수집이 중단되면 안 된다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

MALL_HOST = os.getenv("PDP_MALL_HOST", "https://curaelmall.com")


# ---------- 날짜 ----------

def kst_now():
    return datetime.now(KST)


def kst_today():
    return kst_now().strftime("%Y-%m-%d")


def kst_date(days_ago=0):
    return (kst_now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ---------- 파일 ----------

def load_json(path, default=None):
    """없으면 default(기본 {}). vegicel build_dashboard.py:52 관례."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else default


def save_json(path, obj):
    """임시 파일에 쓰고 os.replace — 중단돼도 기존 파일이 깨지지 않는다."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- 상품번호 파싱 ----------

_RE_QUERY = re.compile(r"[?&]product_no=(\d+)")
_RE_PATH = re.compile(r"/product/(?:[^/]+/)?(\d+)(?:/|$)")
_RE_SHOP = re.compile(r"^/shop\d*")


def parse_product_no(url_or_path):
    """3분산 URL 어느 형태든 상품번호를 뽑는다. 실패 시 None.

    - /product/detail.html?product_no=26
    - /product/<한글슬러그>/26/category/54/display/1/
    - /shop1/product/detail.html?product_no=26
    """
    if not url_or_path:
        return None
    s = str(url_or_path)
    try:
        s = urllib.parse.unquote(s)
    except Exception:
        pass

    m = _RE_QUERY.search(s)
    if m:
        return m.group(1)

    # 경로만 떼어내고 멀티쇼핑몰 접두어 제거
    path = s
    if "://" in path:
        try:
            path = urllib.parse.urlsplit(path).path
        except Exception:
            pass
    path = path.split("?")[0]
    path = _RE_SHOP.sub("", path)

    m = _RE_PATH.search(path)
    if m:
        return m.group(1)
    return None


# ---------- HTTP ----------

def http_get(url, headers=None, tries=3, timeout=25):
    """urllib GET. 429/5xx는 지수 백오프 재시도. 바이트를 반환."""
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
    if last:
        raise last


def http_get_text(url, headers=None, tries=3, timeout=25):
    return http_get(url, headers, tries, timeout).decode("utf-8", "replace")
