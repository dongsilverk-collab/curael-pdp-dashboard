"""Microsoft Clarity Data Export API 클라이언트.

공식 제약 (learn.microsoft.com/clarity/setup-and-installation/clarity-data-export-api):
  - numOfDays 는 1, 2, 3 만
  - 하루 프로젝트당 10회
  - 차원 최대 3개
  - 응답 1,000행, 페이지네이션 없음
  - 응답은 UTC 기준 (호출 시각으로부터 지난 24/48/72시간)

차원: Browser, Device, Country/Region, OS, Source, Medium, Campaign, Channel, URL
지표: Scroll Depth, Engagement Time, Traffic, Popular Pages, Dead Click Count,
      Excessive Scroll, Rage Click Count, Quickback Click, Script Error Count,
      Error Click Count
※ 커스텀 태그(product_no 등)는 차원으로 쓸 수 없다 → URL에서 상품번호를 파싱한다.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from pdp_common import kst_today, load_json, save_json

ENDPOINT = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
BUDGET_PATH = "data/clarity_call_budget.json"
DAILY_LIMIT = 10
TRUNCATE_WARN = 990          # 1000행 상한에 근접하면 잘렸을 수 있다


class ClarityError(RuntimeError):
    pass


class RateLimited(ClarityError):
    pass


def _token():
    """환경변수 우선, 없으면 로컬 .env (gitignore 처리됨).

    토큰을 명령줄에 직접 넣지 않는다 — 셸 히스토리·프로세스 목록에 남는다.
    """
    t = os.getenv("CLARITY_API_TOKEN", "").strip()
    if not t:
        try:
            with open(".env", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("CLARITY_API_TOKEN="):
                        t = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
    if not t:
        raise ClarityError(
            "CLARITY_API_TOKEN 이 없습니다. 환경변수로 넣거나 .env 에 적으세요.")
    return t


# ---------- 호출 예산 ----------

def budget_used(date=None):
    date = date or kst_today()
    return int(load_json(BUDGET_PATH).get(date, 0))


def budget_left(date=None):
    return DAILY_LIMIT - budget_used(date)


def _budget_inc(date=None):
    date = date or kst_today()
    b = load_json(BUDGET_PATH)
    b[date] = int(b.get(date, 0)) + 1
    # 오래된 기록은 정리
    for k in sorted(b)[:-30]:
        b.pop(k, None)
    save_json(BUDGET_PATH, b)


# ---------- 호출 ----------

def live_insights(num_of_days=1, dims=(), reserve=0):
    """차원 조합 1건 조회. 429는 재시도하지 않는다(일일 10회를 태우면 안 된다).

    reserve: 이 호출 후 남겨둬야 할 예산. 사람이 수동 조회할 몫을 지키기 위함.
    """
    if budget_left() <= reserve:
        raise ClarityError(
            f"오늘 호출 예산 소진 (사용 {budget_used()}/{DAILY_LIMIT}, 예약 {reserve})")

    if num_of_days not in (1, 2, 3):
        raise ValueError("numOfDays 는 1, 2, 3 만 허용됩니다.")
    if len(dims) > 3:
        raise ValueError("차원은 최대 3개입니다.")

    params = {"numOfDays": str(num_of_days)}
    for i, d in enumerate(dims, start=1):
        params[f"dimension{i}"] = d
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            _budget_inc()      # 서버에 도달한 경우에만 소진으로 센다
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _budget_inc()          # HTTP 오류도 서버에 도달한 것 → 소진
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code == 429:
            raise RateLimited(f"일일 호출 한도 초과: {body}")
        raise ClarityError(f"HTTP {e.code}: {body}")


def flatten(payload, dims):
    """[{metricName, information:[{...}]}] → {metric: [row,...]} + 잘림 여부."""
    out, truncated = {}, False
    for block in payload or []:
        name = block.get("metricName") or "?"
        rows = block.get("information") or []
        if len(rows) >= TRUNCATE_WARN:
            truncated = True
        out[name] = rows
    return out, truncated
