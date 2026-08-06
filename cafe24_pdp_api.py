"""카페24 관리자 주문 API — 상품별 매출용.

vegicel-ad-autopilot/cafe24_api.py 의 인증·요청 패턴을 그대로 따르되 두 가지가 다르다.

**① 토큰 파일을 공유한다(복사 금지).**
카페24 refresh 토큰은 **쓸 때마다 새 값으로 교체**된다. 토큰 파일을 두 저장소에 복사해
각자 갱신하면, 먼저 갱신한 쪽이 다른 쪽의 refresh 토큰을 무효화한다. 그러면 2주 만료를
기다릴 것도 없이 즉시 재인증(브라우저 로그인)이 필요해진다.
그래서 `CAFE24_TOKEN_PATH` 로 **원본 파일 하나**를 가리킨다.

**② 그래서 이 수집기는 로컬 전용이다.**
Actions 에서 돌리면 클라우드와 로컬이 같은 refresh 토큰을 두고 경합한다. 워크플로에
넣지 않는다.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_VER = os.getenv("CAFE24_API_VERSION", "2026-03-01")


class Cafe24Error(RuntimeError):
    pass


def _env(name, required=True):
    v = (os.getenv(name) or "").strip()
    if not v:
        try:
            with open(".env", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(name + "="):
                        v = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
    if not v and required:
        raise Cafe24Error("%s 가 없습니다 (.env 또는 환경변수)" % name)
    return v


def _base():
    return "https://%s.cafe24api.com/api/v2" % _env("CAFE24_MALL_ID")


def token_path():
    return _env("CAFE24_TOKEN_PATH")


def _basic_auth():
    cid, sec = _env("CAFE24_CLIENT_ID"), _env("CAFE24_CLIENT_SECRET")
    return "Basic " + base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()


def _save_token(tok):
    p = token_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tok, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)   # 중단돼도 토큰 파일이 깨지지 않게


def get_access_token():
    """저장된 토큰을 쓰고, 만료 임박이면 refresh 로 갱신해 **같은 파일**에 되쓴다."""
    from datetime import datetime, timedelta
    p = token_path()
    if not os.path.exists(p):
        raise Cafe24Error(
            "토큰 파일이 없습니다: %s\n"
            "vegicel-ad-autopilot 에서 최초 발급이 필요합니다." % p)
    with open(p, encoding="utf-8") as f:
        tok = json.load(f)

    try:
        exp = datetime.fromisoformat(tok.get("expires_at", "").replace("Z", "+00:00"))
        now = datetime.now(exp.tzinfo) if exp.tzinfo else datetime.now()
        if exp > now + timedelta(minutes=10):
            return tok["access_token"]
    except Exception:
        pass

    req = urllib.request.Request(
        "%s/oauth/token" % _base(),
        data=urllib.parse.urlencode({"grant_type": "refresh_token",
                                     "refresh_token": tok["refresh_token"]}).encode(),
        headers={"Authorization": _basic_auth(),
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            new = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise Cafe24Error("토큰 갱신 실패: %s" % e.read().decode("replace")[:300])
    _save_token(new)
    return new["access_token"]


def get(path, params, tries=4):
    url = "%s%s?%s" % (_base(), path, urllib.parse.urlencode(params))
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer %s" % get_access_token(),
            "X-Cafe24-Api-Version": API_VER,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                time.sleep(0.7)          # 레이트리밋 예방
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode("replace")
            if e.code == 429 and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise Cafe24Error("HTTP %s: %s" % (e.code, body[:300]))
        except (ConnectionResetError, urllib.error.URLError, OSError) as e:
            if attempt < tries - 1:
                print("  [재시도] 연결 끊김 — %d초 대기" % (5 * (attempt + 1)),
                      file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            raise Cafe24Error("연결 실패: %s" % e)
