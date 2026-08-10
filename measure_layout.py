"""상세영역이 페이지의 몇 % 지점인지 실측해 data/pdp_layout.json 에 남긴다.

왜 필요한가:
  Clarity 의 평균 스크롤 깊이는 **페이지 전체** 기준인데, 우리 구간 표는
  **상세영역(#prdDetail)** 기준이다. 좌표계가 달라 그대로 겹치면 조용히 틀린다.
  detail_start/end 를 알면 Clarity 값을 상세영역 좌표로 옮길 수 있다:
      상세영역_% = (페이지_% - start) / (end - start)

왜 별도 스크립트인가:
  페이지 안에서 상단(가격·옵션·리뷰탭) 높이는 HTML만 봐서는 알 수 없다.
  실제로 렌더링해야 나오므로 브라우저가 필요하고, 그래서 CI 에서 매일 돌릴 수 없다.
  대신 레이아웃은 거의 안 바뀌므로 가끔 수동으로 갱신하면 된다.
  측정 날짜를 함께 남겨 낡았는지 화면에서 알 수 있게 한다.

사용: 브라우저에서 아래 JS 를 각 상품 페이지에서 실행한 결과를 --set 으로 넣는다.
      python measure_layout.py --set 26 12.3 68.4
"""
import argparse
import pdp_common as C

PATH = "data/pdp_layout.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs=3, metavar=("PID", "START", "END"),
                    help="상품번호, 상세영역 시작%%, 끝%%")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    store = C.load_json(PATH, {})
    if a.set:
        pid, s, e = a.set[0], float(a.set[1]), float(a.set[2])
        store[pid] = {"detail_start_pct": s, "detail_end_pct": e,
                      "measured_at": C.kst_today()}
        C.save_json(PATH, store)
        print("%s 저장: 상세영역 %.1f%% ~ %.1f%%" % (pid, s, e))
    if a.show or not a.set:
        for pid in sorted(store, key=lambda x: int(x) if x.isdigit() else 0):
            v = store[pid]
            print("  %-4s %5.1f%% ~ %5.1f%%  (측정 %s)"
                  % (pid, v["detail_start_pct"], v["detail_end_pct"], v["measured_at"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
