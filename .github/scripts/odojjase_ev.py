"""오도짜세 플레이 기록(Firestore ev, 게임 1.5.1부터 — 이름을 정한 사람만 보낸다) 읽기·요약. odojjase_stats.py가 부른다.

ev는 규칙(firestore.rules)이 공개 읽기를 막고 수집기 전용 익명 계정 하나만 읽게 연다. 그 계정의 refresh 토큰을
저장소 비밀값 ODOJJASE_EV_REFRESH로 받는다. 없으면 {"ok": false}만 돌려준다(페이지는 그 칸만 비워 둔다).

읽기 한도 때문에 명부처럼 모아 둔 줄(EV_CACHE)에 새 줄만 덧붙인다. 이 파일은 공개 가지에 올라가므로
uid는 비밀값으로 섞은 짧은 열쇠로 바꿔 둔다 — 공개 명부의 uid·닉네임과 이어 붙일 수 없게(누가 무엇을 사려 했는지가 드러나지 않게).
페이지에는 요약(latest.json의 ev)만 보인다.
"""
import collections, hashlib, hmac, json, os, re, urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = "AIzaSyCWkmK0EDYP_cQ-kxQPUPKFb1TS2i0aspY"  # 게임에 박혀 나가는 공개 웹 키(hall.gd와 같다)
FS = "https://firestore.googleapis.com/v1/projects/odojjase/databases/(default)/documents"
KST = timezone(timedelta(hours=9))
EV_CACHE = "ev_rows.json"
COLS = ["id", "ct", "u", "e", "ver", "os", "n", "m", "d", "op", "i", "k", "secs", "why", "win", "rev", "q", "s", "y", "p", "r", "rd", "pr", "first", "c", "md", "dep"]
OVERLAP = timedelta(minutes=30)   # 규칙이 시각을 서버 시계 ±10분까지 받는다
FAILS = 20


def _post(url, body, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    with urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), h), timeout=30) as r:
        return json.load(r)


def _token(refresh):
    with urllib.request.urlopen(urllib.request.Request("https://securetoken.googleapis.com/v1/token?key=" + API_KEY,
                                                       ("grant_type=refresh_token&refresh_token=" + refresh).encode(),
                                                       {"Content-Type": "application/x-www-form-urlencoded"}), timeout=30) as r:
        return json.load(r)["id_token"]


def _val(f):
    k, v = next(iter(f.items()))
    return int(v) if k == "integerValue" else v


def _row(d, salt):
    f = {k: _val(v) for k, v in d.get("fields", {}).items()}
    if not isinstance(f.get("uid"), str) or not isinstance(f.get("e"), str):
        return None
    r = {k: f.get(k) for k in COLS if k in f}
    r["id"] = d["name"].split("/")[-1]
    r["ct"] = d.get("createTime", f.get("at", ""))
    r["u"] = hmac.new(salt, f["uid"].encode(), hashlib.sha256).hexdigest()[:12]
    for k in ("why", "k", "p", "ver"):
        if isinstance(r.get(k), str):
            r[k] = r[k][:40]
    return r


def _since(tok, salt, since):
    out, seen = [], set()
    while True:
        q = {"from": [{"collectionId": "ev"}], "orderBy": [{"field": {"fieldPath": "at"}, "direction": "ASCENDING"}], "limit": 500,
             "where": {"fieldFilter": {"field": {"fieldPath": "at"}, "op": "GREATER_THAN_OR_EQUAL", "value": {"timestampValue": since}}}}
        docs = [e["document"] for e in _post(FS + ":runQuery", {"structuredQuery": q}, tok) if isinstance(e, dict) and "document" in e]
        fresh = 0
        for d in docs:
            r = _row(d, salt)
            if r and r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
                fresh += 1
        if len(docs) < 500 or not fresh:
            return out
        since = docs[-1]["fields"]["at"]["timestampValue"]


def _t(ct):
    return datetime.fromisoformat(re.sub(r"\.\d+", "", ct).replace("Z", "+00:00")).astimezone(KST)


def read(out, errors):
    refresh = os.environ.get("ODOJJASE_EV_REFRESH", "").strip()
    if not refresh:
        return {"ok": False, "why": "읽기 열쇠(저장소 비밀값 ODOJJASE_EV_REFRESH)가 아직 없다"}
    salt = hashlib.sha256(("odojjase-ev:" + refresh).encode()).digest()
    path = os.path.join(out, EV_CACHE)
    rows = {}
    try:
        c = json.load(open(path))
        if c.get("cols") == COLS:
            rows = {r[0]: dict(zip(COLS, r)) for r in c["rows"]}
    except Exception:
        pass
    tok = _token(refresh)
    last = max((_t(r["ct"]) for r in rows.values()), default=None)
    since = "2000-01-01T00:00:00Z" if last is None else (last - OVERLAP).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in _since(tok, salt, since):
        rows[r["id"]] = r
    body = sorted(rows.values(), key=lambda r: (r["ct"], r["id"]))
    json.dump({"cols": COLS, "rows": [[r.get(k) for k in COLS] for r in body]}, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    return summary(body)


# 집계에서 빼는 사람(2026-11-26 사용자 "내 결제는 다 빼"): 사장님 아이폰(TestFlight 시험 구매·출시 전 버전). 값은 섞은 uid(r["u"])
SKIP_U = {"99625a202e4b"}


def summary(rows):
    rows = [r for r in rows if r.get("u") not in SKIP_U]
    s = {"ok": True, "rows": len(rows), "since": _t(rows[0]["ct"]).isoformat(timespec="seconds") if rows else ""}
    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r["u"], []).append(r)
    s["people"] = len(by)
    latest = lambda rs, k: next((r[k] for r in reversed(rs) if r.get(k)), "")
    s["os"] = dict(collections.Counter(latest(rs, "os") for rs in by.values()))
    s["ver"] = dict(collections.Counter(latest(rs, "ver") for rs in by.values()))

    # 날짜별(한국 날짜)
    days = collections.OrderedDict()
    first_day = {u: _t(rs[0]["ct"]).date().isoformat() for u, rs in by.items() if (rs[0].get("n") or 0) == 0}
    for r in rows:
        d = _t(r["ct"]).date().isoformat()
        e = days.setdefault(d, {"d": d, "people": set(), "new": set(), "runs": 0, "ends": 0, "kk": 0, "shop": 0, "short": 0, "tap": 0, "buy": 0, "fail": 0, "rev": 0, "rev_y": 0,
                                "p_a": set(), "p_i": set(), "n_a": set(), "n_i": set(), "runs_a": 0, "runs_i": 0})
        e["people"].add(r["u"])
        o = "i" if r.get("os") == "i" else "a"          # 운영체제별(2026-11-25 사용자): 안드로이드·iOS
        e["p_" + o].add(r["u"])
        if first_day.get(r["u"]) == d:
            e["new"].add(r["u"])
            e["n_" + o].add(r["u"])
        kind = r["e"]
        if kind == "run":
            e["runs"] += 1
            e["runs_" + o] += 1
        elif kind == "end": e["ends"] += 1
        elif kind == "kk": e["kk"] += 1
        elif kind == "shop": e["shop"] += 1
        elif kind == "short": e["short"] += 1
        elif kind == "rev":
            e["rev"] += 1
            e["rev_y"] += bool(r.get("y"))
        elif kind == "buy":
            res = r.get("r")
            if res == "tap": e["tap"] += 1
            elif res == "ok": e["buy"] += 1
            elif res == "fail": e["fail"] += 1
    s["days"] = [dict(v, people=len(v["people"]), new=len(v["new"]), p_a=len(v["p_a"]), p_i=len(v["p_i"]), n_a=len(v["n_a"]), n_i=len(v["n_i"])) for v in reversed(days.values())]

    # 시간대별(2026-11-24 사용자 "시간대별 플레이 추이"): 한국 시각 0~23시마다 기록을 남긴 사람 수(한 사람은 그 시간에 한 번)·출격 수.
    # today = 오늘, avg7 = 오늘 앞 7일 평균(그날 기록이 있던 날만 나눈다)
    hp = collections.defaultdict(set)      # (날짜, 시) → 사람
    hr = collections.Counter()             # (날짜, 시) → 출격
    for r in rows:
        t = _t(r["ct"])
        k = (t.date().isoformat(), t.hour)
        hp[k].add(r["u"])
        if r["e"] == "run":
            hr[k] += 1
    today = datetime.now(KST).date()
    prev = [(today - timedelta(days=i)).isoformat() for i in range(1, 8)]
    prev = [d for d in prev if any((d, h) in hp for h in range(24))]
    td = today.isoformat()
    s["hours"] = {"today": [len(hp.get((td, h), ())) for h in range(24)], "today_runs": [hr.get((td, h), 0) for h in range(24)],
                  "avg7": [round(sum(len(hp.get((d, h), ())) for d in prev) / len(prev), 1) if prev else 0 for h in range(24)],
                  "avg7_runs": [round(sum(hr.get((d, h), 0) for d in prev) / len(prev), 1) if prev else 0 for h in range(24)],
                  "ndays": len(prev), "now": datetime.now(KST).hour}
    # 전일·전주 같은 요일(2026-11-26 사용자). 기록이 없던 날이면 빈 목록
    for key, back in (("yday", 1), ("wk", 7)):
        d = (today - timedelta(days=back)).isoformat()
        has = any((d, h) in hp for h in range(24))
        s["hours"][key] = [len(hp.get((d, h), ())) for h in range(24)] if has else []
        s["hours"][key + "_runs"] = [hr.get((d, h), 0) for h in range(24)] if has else []
        s["hours"][key + "_d"] = d

    # 새로 온 사람(첫 줄에서 끝낸 판 0)의 길
    new = {u: rs for u, rs in by.items() if (rs[0].get("n") or 0) == 0}
    has = lambda rs, f: any(f(r) for r in rs)
    f = {"new": len(new), "run": 0, "tut": 0, "tut_skip": 0, "end1": 0, "isl1": 0, "isl3": 0, "boss": 0, "back": 0}
    first_isl, first_why = collections.Counter(), collections.Counter()
    for rs in new.values():
        f["run"] += has(rs, lambda r: r["e"] == "run")
        f["tut"] += has(rs, lambda r: r["e"] == "tut")
        f["tut_skip"] += has(rs, lambda r: r["e"] == "tut" and r.get("s") == "skip")
        f["isl1"] += has(rs, lambda r: r["e"] in ("clr", "end") and (r.get("i") or 0) >= 1)
        f["isl3"] += has(rs, lambda r: r["e"] in ("clr", "end") and (r.get("i") or 0) >= 3)
        f["boss"] += has(rs, lambda r: r["e"] == "clr" and r.get("k") == "boss" and (r.get("op") or 0) == 0)
        f["back"] += len({_t(r["ct"]).date() for r in rs}) >= 2
        e1 = next((r for r in rs if r["e"] == "end"), None)
        if e1:
            f["end1"] += 1
            first_isl[str(e1.get("i") or 0)] += 1
            if not e1.get("win"):
                first_why[e1.get("why") or ("빤스RUN" if e1.get("q") else "?")] += 1
    s["funnel"] = f
    s["first_islands"] = dict(sorted(first_isl.items(), key=lambda kv: int(kv[0])))
    s["first_why"] = first_why.most_common(12)

    # 판 도중 사라진 사람: 마지막 줄이 출격·섬 확보(판 끝·결과 없이 끝)
    gone = collections.Counter()
    ran = 0
    for rs in by.values():
        if not has(rs, lambda r: r["e"] == "run"):
            continue
        ran += 1
        last = [r for r in rs if r["e"] in ("run", "clr", "end", "tut")][-1]
        if last["e"] in ("run", "clr", "tut") and _t(rs[-1]["ct"]) < datetime.now(KST) - timedelta(hours=2):   # 지금 하고 있는 사람은 빼려고 두 시간
            gone["%s:%d" % (last.get("k") or "battle", last.get("i") or 0)] += 1
    s["gone"] = {"n": sum(gone.values()), "of": ran, "where": gone.most_common(10)}

    s["reach"] = dict(sorted(collections.Counter(str(r.get("i") or 0) for r in rows if r["e"] == "end").items(), key=lambda kv: int(kv[0])))
    rev = [r for r in rows if r["e"] == "rev"]
    s["rev"] = {"offers": len(rev), "yes": sum(1 for r in rev if r.get("y"))}

    # 결제
    who = lambda f: len({r["u"] for r in rows if f(r)})
    prod = collections.defaultdict(lambda: {"tap": 0, "ok": 0, "cancel": 0, "fail": 0})
    for r in rows:
        if r["e"] == "buy" and r.get("r") in ("tap", "ok", "cancel", "fail"):
            prod[r.get("p") or "?"][r["r"]] += 1
    # 날짜별 상품(2026-11-23 사용자 "상품별 누른 횟수와 결과를 날짜별로"): 새 날짜부터 [{d, p: {상품: {tap, ok, cancel, fail}}}]
    pday = collections.OrderedDict()
    for r in rows:
        if r["e"] == "buy" and r.get("r") in ("tap", "ok", "cancel", "fail"):
            d = pday.setdefault(_t(r["ct"]).date().isoformat(), {})
            d.setdefault(r.get("p") or "?", {"tap": 0, "ok": 0, "cancel": 0, "fail": 0})[r["r"]] += 1
    fails = [{"at": _t(r["ct"]).strftime("%m-%d %H:%M"), "os": r.get("os", ""), "p": r.get("p", ""), "why": r.get("why", ""), "rd": bool(r.get("rd")), "pr": bool(r.get("pr"))}
             for r in rows if r["e"] == "buy" and r.get("r") == "fail"]
    s["pay"] = {"kk_people": who(lambda r: r["e"] == "kk"), "kk_views": sum(1 for r in rows if r["e"] == "kk"),
                "kk_noprice": sum(1 for r in rows if r["e"] == "kk" and not r.get("pr")),
                "shop_people": who(lambda r: r["e"] == "shop"), "short_people": who(lambda r: r["e"] == "short"),
                "tap_people": who(lambda r: r["e"] == "buy" and r.get("r") == "tap"), "buy_people": who(lambda r: r["e"] == "buy" and r.get("r") == "ok"),
                "products": dict(prod), "by_day": [{"d": d, "p": v} for d, v in reversed(pday.items())], "fails": fails[-FAILS:][::-1]}
    # 섬 종류(임무)별(2026-11-26 사용자 "어떤 미션이 인기가 없었나"): 확보·그 섬에서 끝남·걸린 시간·앱 끔(bg, 1.6부터)·지도에서 고른 비율(pick, 1.6.1부터)
    ks = collections.defaultdict(lambda: {"clr": 0, "end": 0, "quit": 0, "dur": [], "bg": 0, "picked": 0, "offered": 0})
    for u, rs in by.items():
        prev = 0
        for r in rs:
            e, k = r["e"], r.get("k") or ""
            if e == "run":
                prev = 0
            elif e in ("clr", "end") and k and k != "tutorial":
                sec = int(r.get("secs") or 0)
                if e == "clr" or r.get("win"):      # 마지막 섬은 확보 줄 없이 이긴 판 끝으로 남는다
                    ks[k]["clr"] += 1
                    if sec >= prev:
                        ks[k]["dur"].append(sec - prev)
                    prev = sec
                else:
                    ks[k]["end"] += 1
                    ks[k]["quit"] += bool(r.get("q"))
                    prev = 0
            elif e == "bg" and k and k != "tutorial":
                ks[k]["bg"] += 1
            elif e == "pick":
                offered = [x for x in str(r.get("p") or "").split(",") if x]
                if len(offered) >= 2:
                    for x in set(offered):
                        ks[x]["offered"] += 1
                    if k:
                        ks[k]["picked"] += 1
    order = ["battle", "rescue", "supply", "salvage", "laps", "boss", "final"]
    s["kinds"] = [{"k": k, "clr": v["clr"], "end": v["end"], "quit": v["quit"], "bg": v["bg"], "picked": v["picked"], "offered": v["offered"],
                   "dur": sorted(v["dur"])[len(v["dur"]) // 2] if v["dur"] else None}
                  for k, v in sorted(ks.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)]
    return s
