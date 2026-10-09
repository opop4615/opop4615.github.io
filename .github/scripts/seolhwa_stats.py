#!/usr/bin/env python3
"""설화던전 현황 수집기. GitHub Actions(.github/workflows/seolhwa-stats.yml)가 한 시간마다 돌린다.

  python3 seolhwa_stats.py <출력 폴더>

출력 폴더에 latest.json(지금 값 전부)을 쓰고 history.jsonl(숫자만, 한 줄 = 한 번 수집)에 한 줄 덧붙인다.
페이지(seolhwa-stats/index.html)는 이 둘을 읽어 "달라진 것"을 만든다.
브라우저가 직접 못 읽는 곳(플레이·앱스토어 차트·리뷰)은 여기서만 읽는다.

한 곳이 실패해도 나머지는 계속한다 — 실패는 errors에 적고, 그 값은 비운다(페이지가 "못 읽음"으로 보인다).
YT_API_KEY 환경변수가 있으면 유튜브 Data API로 구독자·전체 영상을, 없으면 채널 RSS(최근 15편 조회수)만 읽는다.
"""
import json, os, re, sys, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

IOS_ID = 6814757012
PLAY_ID = "com.sungkisoft.seolhwadungeon"
YT_HANDLE = "sungkisoft"
FS_DOCS = "https://firestore.googleapis.com/v1/projects/seolhwa-dungeon/databases/(default)/documents"
FS_KEY = "AIzaSyCHvKcIPGZKwk5lwPmvg7AuU1iAhuMheRw"  # 게임에 박혀 나가는 공개 웹 키(hall.gd와 같다). 막는 일은 firestore.rules가 한다
KST = timezone(timedelta(hours=9))
LAUNCH = datetime(2026, 10, 2, tzinfo=KST)  # 전당 "출시 뒤"를 세는 날 (marketing/calendar.md 점검과 같은 기준)
KEEP = 24 * 21  # history.jsonl에 남길 줄 수(약 3주)

# 검색 순위를 볼 낱말. 페이지(index.html)의 같은 표와 맞춘다
IOS_TERMS = {"kr": ["정통 로그라이크", "던전크롤", "던전", "로그라이크", "한국 설화", "설화"],
             "us": ["Seolhwa Dungeon", "korean folklore", "roguelike", "dungeon crawl", "roguelike rpg", "dokkaebi"]}
PLAY_TERMS = {"kr": ["정통 로그라이크", "던전크롤", "로그라이크", "설화"],
              "us": ["Seolhwa Dungeon", "korean folklore roguelike", "roguelike"]}
# 애플 차트 (아이폰 무료, 장르 번호). 200위까지만 준다
CHARTS = [("kr", 6014, "게임"), ("kr", 7014, "롤플레잉"), ("us", 6014, "게임"), ("us", 7014, "롤플레잉")]
# 플레이 설명문에 장르 낱말이 들어갔는지 (2026-10-09: 옛 글엔 셋 다 0번이었다)
GENRE_WORDS = {"kr": ["로그라이크", "턴제", "RPG", "던전 크롤", "도트"], "us": ["roguelike", "turn-based", "RPG", "dungeon crawl", "pixel"]}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

errors = []


def get(url, headers=None, tries=3):
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def step(name, fn, *a):
    """한 갈래를 돌리고, 실패하면 errors에 남기고 None"""
    try:
        return fn(*a)
    except Exception as e:
        errors.append({"what": name, "why": "%s: %s" % (type(e).__name__, str(e)[:200])})
        traceback.print_exc()
        return None


def hangul(s):
    return bool(re.search("[가-힣]", s or ""))


# ---------------------------------------------------------------- 앱스토어

def ios_lookup(cc):
    d = get_json("https://itunes.apple.com/lookup?id=%d&country=%s" % (IOS_ID, cc))
    if not d.get("results"):
        return {"listed": False}
    r = d["results"][0]
    return {"listed": True, "name": r.get("trackName"), "version": r.get("version"),
            "updated": r.get("currentVersionReleaseDate"), "rating": r.get("averageUserRating"),
            "ratings": r.get("userRatingCount", 0), "english": not hangul(r.get("description", "")[:300]),
            "desc_head": (r.get("description") or "")[:140], "shots": len(r.get("screenshotUrls") or [])}


def ios_rank(cc, term):
    q = urllib.parse.urlencode({"term": term, "country": cc, "entity": "software", "limit": 200})
    rows = get_json("https://itunes.apple.com/search?" + q).get("results", [])
    for i, r in enumerate(rows, 1):
        if r.get("trackId") == IOS_ID:
            return i
    return None


def ios_chart(cc, genre):
    """(순위 또는 None, 목록 길이). 목록이 비면 순위가 아니라 실패다"""
    feed = get_json("https://itunes.apple.com/%s/rss/topfreeapplications/limit=200/genre=%d/json" % (cc, genre))["feed"]
    rows = feed.get("entry", [])
    if not rows:
        raise RuntimeError("빈 목록")
    for i, e in enumerate(rows, 1):
        if e["id"]["attributes"].get("im:id") == str(IOS_ID):
            return i, len(rows)
    return None, len(rows)


def ios_reviews(cc):
    d = get_json("https://itunes.apple.com/%s/rss/customerreviews/page=1/id=%d/sortby=mostrecent/json" % (cc, IOS_ID))
    out = []
    for e in d.get("feed", {}).get("entry", []) or []:
        if "im:rating" not in e:
            continue
        out.append({"rating": int(e["im:rating"]["label"]), "title": e["title"]["label"],
                    "text": e["content"]["label"][:400], "ver": e.get("im:version", {}).get("label", ""),
                    "date": e.get("updated", {}).get("label", "")[:10]})
    return out[:10]


# ---------------------------------------------------------------- 구글 플레이 (google-play-scraper)

def play_app(cc, lang):
    from google_play_scraper import app
    r = app(PLAY_ID, lang=lang, country=cc)
    desc = r.get("description") or ""
    words = GENRE_WORDS["kr" if lang == "ko" else "us"]
    return {"title": r.get("title"), "installs": r.get("installs"), "real": r.get("realInstalls"),
            "score": round(r["score"], 2) if r.get("score") else None, "ratings": r.get("ratings") or 0,
            "reviews": r.get("reviews") or 0, "version": r.get("version"), "updated": r.get("updated"),
            "english": not hangul(desc[:300]), "desc_head": desc[:140],
            "genre_words": {w: desc.lower().count(w.lower()) for w in words}}


def play_rank(cc, lang, term):
    from google_play_scraper import search
    rows = search(term, lang=lang, country=cc, n_hits=30)
    if not rows:
        raise RuntimeError("빈 결과")
    for i, r in enumerate(rows, 1):
        if r.get("appId") == PLAY_ID:
            return i
    return None


# ---------------------------------------------------------------- 유튜브

def yt_api(path, **q):
    q["key"] = os.environ["YT_API_KEY"]
    return get_json("https://www.googleapis.com/youtube/v3/%s?%s" % (path, urllib.parse.urlencode(q)))


def youtube():
    if os.environ.get("YT_API_KEY"):
        ch = yt_api("channels", part="snippet,statistics,contentDetails", forHandle=YT_HANDLE)["items"][0]
        up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        ids, tok = [], ""
        for _ in range(4):
            q = {"part": "contentDetails", "playlistId": up, "maxResults": 50}
            if tok:
                q["pageToken"] = tok
            d = yt_api("playlistItems", **q)
            ids += [x["contentDetails"]["videoId"] for x in d.get("items", [])]
            tok = d.get("nextPageToken", "")
            if not tok:
                break
        vids = []
        for i in range(0, len(ids), 50):
            for v in yt_api("videos", part="snippet,statistics", id=",".join(ids[i:i + 50])).get("items", []):
                s = v.get("statistics", {})
                vids.append({"id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
                             "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))})
        others = []
        try:
            d = yt_api("search", part="snippet", q='설화던전|"Seolhwa Dungeon"', type="video", maxResults=25, order="date")
            others = [{"id": x["id"]["videoId"], "title": x["snippet"]["title"], "channel": x["snippet"]["channelTitle"],
                       "published": x["snippet"]["publishedAt"]} for x in d.get("items", [])
                      if x["snippet"]["channelId"] != ch["id"]]
        except Exception as e:
            errors.append({"what": "유튜브 다른 채널 검색", "why": str(e)[:200]})
        st = ch["statistics"]
        return {"via": "api", "channel_id": ch["id"], "subs": int(st.get("subscriberCount", 0)),
                "total_views": int(st.get("viewCount", 0)), "videos": vids, "others": others}
    # 키가 없으면: 채널 페이지에서 채널 번호 → RSS(최근 15편, 조회수는 들어 있다)
    page = get("https://www.youtube.com/@%s?hl=en&gl=US" % YT_HANDLE, headers={"Cookie": "CONSENT=YES+cb; SOCS=CAI"})
    m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page)
    if not m:
        raise RuntimeError("채널 번호를 못 찾았다")
    cid = m.group(1)
    subs = None
    s = re.search(r'([\d][\d.,]*\s?[KM]?)\s+subscribers?\b', page)
    if s:
        n = s.group(1).replace(",", "").replace(" ", "")
        subs = int(float(n[:-1]) * (1000 if n[-1] == "K" else 1000000)) if n[-1] in "KM" else int(float(n))
    else:
        i = page.find("subscriber")
        errors.append({"what": "유튜브 구독자 수(키 없이)", "why": "채널 페이지에서 못 찾음 — 길이 %d, 주변: %s" % (len(page), page[max(0, i - 80):i + 40].replace("\n", " ") if i >= 0 else "없음")})
    xml = get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid)
    vids = []
    for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", ent).group(1)
        title = re.search(r"<title>(.*?)</title>", ent, re.S).group(1)
        pub = re.search(r"<published>(.*?)</published>", ent).group(1)
        views = re.search(r'<media:statistics views="(\d+)"', ent)
        vids.append({"id": vid, "title": unescape(title), "published": pub, "views": int(views.group(1)) if views else None})
    return {"via": "rss", "channel_id": cid, "subs": subs, "total_views": None, "videos": vids, "others": None}


def unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


# ---------------------------------------------------------------- 명예의 전당 (Firestore, 읽기는 공개)

def fs_val(f):
    k, v = next(iter(f.items()))
    if k == "mapValue":
        return {a: fs_val(b) for a, b in v.get("fields", {}).items()}
    if k == "arrayValue":
        return [fs_val(x) for x in v.get("values", [])]
    if k == "integerValue":
        return int(v)
    return v


def hall():
    fields = ["uid", "name", "at", "depth", "dungeon", "by", "ver", "species", "cls", "win", "score", "turn", "detail.story"]
    docs, tok = [], ""
    while True:
        q = [("key", FS_KEY), ("pageSize", "300")] + [("mask.fieldPaths", f) for f in fields]
        if tok:
            q.append(("pageToken", tok))
        d = get_json(FS_DOCS + "/hall?" + urllib.parse.urlencode(q))
        docs += d.get("documents", [])
        tok = d.get("nextPageToken", "")
        if not tok:
            break
    rows = []
    for d in docs:
        r = {k: fs_val(v) for k, v in d.get("fields", {}).items()}
        if "at" not in r:
            continue
        t = datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(KST)
        if t < LAUNCH:
            continue
        story = " ".join((r.get("detail") or {}).get("story", []))
        rows.append({"t": t, "uid": r.get("uid", ""), "name": r.get("name", ""), "depth": r.get("depth", 0),
                     "dungeon": r.get("dungeon", ""), "by": r.get("by", ""), "ver": r.get("ver", ""),
                     "species": r.get("species", ""), "cls": r.get("cls", ""), "win": r.get("win", False),
                     "en": bool(story) and not hangul(story)})
    rows.sort(key=lambda r: r["t"])
    today = datetime.now(KST).date()
    first = {}
    for r in rows:
        first.setdefault(r["uid"], r["t"].date())
    days = {}
    for r in rows:
        k = r["t"].date().isoformat()
        e = days.setdefault(k, {"runs": 0, "people": set(), "new": set()})
        e["runs"] += 1
        e["people"].add(r["uid"])
        if first[r["uid"]] == r["t"].date():
            e["new"].add(r["uid"])
    tr = [r for r in rows if r["t"].date() == today]
    back = sum(1 for u, f in first.items() if f < today and len({r["t"].date() for r in rows if r["uid"] == u}) > 1)
    return {"total_runs": len(rows), "total_people": len(first), "today_runs": len(tr),
            "today_people": len({r["uid"] for r in tr}), "today_new": sum(1 for f in first.values() if f == today),
            "en_runs": sum(r["en"] for r in rows), "wins": sum(1 for r in rows if r["win"]),
            "returned": back, "returned_of": sum(1 for f in first.values() if f < today),
            "days": [{"day": k, "runs": v["runs"], "people": len(v["people"]), "new": len(v["new"])} for k, v in sorted(days.items())],
            "recent": [{"at": r["t"].isoformat(), "name": r["name"], "who": "%s %s" % (r["species"], r["cls"]),
                        "dungeon": r["dungeon"], "depth": r["depth"], "by": r["by"], "ver": r["ver"], "win": r["win"]}
                       for r in rows[-15:][::-1]]}


# ---------------------------------------------------------------- 묶기

def metrics(s):
    """history.jsonl 한 줄에 들어갈 숫자들. 열쇠는 페이지(index.html)의 LABEL과 맞춘다"""
    m = {}
    h = s.get("hall") or {}
    for k in ["total_runs", "total_people", "today_runs", "today_people", "today_new", "en_runs"]:
        if k in h:
            m["hall." + k] = h[k]
    for cc in ["kr", "us"]:
        a = (s.get("ios") or {}).get(cc) or {}
        if a.get("listed"):
            m["ios.%s.ratings" % cc] = a.get("ratings", 0)
            if a.get("rating") is not None:
                m["ios.%s.rating" % cc] = round(a["rating"], 2)
        p = (s.get("play") or {}).get(cc) or {}
        if p.get("real") is not None:
            m["play.%s.real" % cc] = p["real"]
        if p.get("ratings") is not None and p:
            m["play.%s.ratings" % cc] = p["ratings"]
    for k, v in (s.get("ios_rank") or {}).items():
        m["ios.rank." + k] = v
    for k, v in (s.get("play_rank") or {}).items():
        m["play.rank." + k] = v
    for k, v in (s.get("charts") or {}).items():
        m["chart." + k] = v
    y = s.get("youtube") or {}
    if y.get("subs") is not None:
        m["yt.subs"] = y["subs"]
    for v in y.get("videos") or []:
        if v.get("views") is not None:
            m["yt.v." + v["id"]] = v["views"]
    return m


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    s = {"at": datetime.now(KST).isoformat(timespec="seconds")}
    s["hall"] = step("명예의 전당", hall)
    s["ios"] = {cc: step("앱스토어 %s 페이지" % cc, ios_lookup, cc) for cc in ["kr", "us"]}
    s["ios_rank"] = {}
    for cc, terms in IOS_TERMS.items():
        for t in terms:
            try:
                s["ios_rank"]["%s.%s" % (cc, t)] = ios_rank(cc, t)
            except Exception as e:
                errors.append({"what": "앱스토어 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
            time.sleep(1)
    s["charts"], s["charts_n"] = {}, {}
    for cc, g, name in CHARTS:
        try:
            s["charts"]["%s.%d" % (cc, g)], s["charts_n"]["%s.%d" % (cc, g)] = ios_chart(cc, g)
        except Exception as e:
            errors.append({"what": "앱스토어 차트 %s %s" % (cc, name), "why": str(e)[:200]})
    s["reviews"] = {cc: step("앱스토어 %s 리뷰" % cc, ios_reviews, cc) for cc in ["kr", "us"]}
    s["play"] = {"kr": step("플레이 한국 페이지", play_app, "kr", "ko")}
    time.sleep(2)
    s["play"]["us"] = step("플레이 미국 페이지", play_app, "us", "en")
    s["play_rank"] = {}
    for cc, terms in PLAY_TERMS.items():
        for t in terms:
            time.sleep(2)  # 몰아서 읽으면 구글이 503으로 막는다 (2026-10-09)
            try:
                s["play_rank"]["%s.%s" % (cc, t)] = play_rank(cc, "ko" if cc == "kr" else "en", t)
            except Exception as e:
                errors.append({"what": "플레이 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
    s["youtube"] = step("유튜브", youtube)
    s["errors"] = errors
    s["m"] = metrics(s)
    json.dump(s, open(os.path.join(out, "latest.json"), "w"), ensure_ascii=False, default=str)
    hp = os.path.join(out, "history.jsonl")
    lines = open(hp).read().splitlines() if os.path.exists(hp) else []
    lines.append(json.dumps({"at": s["at"], "m": s["m"]}, ensure_ascii=False, separators=(",", ":")))
    open(hp, "w").write("\n".join(lines[-KEEP:]) + "\n")
    print("수집 끝: 값 %d개, 못 읽은 것 %d개" % (len(s["m"]), len(errors)))
    for e in errors:
        print("  못 읽음 · %s · %s" % (e["what"], e["why"]))


if __name__ == "__main__":
    main()
