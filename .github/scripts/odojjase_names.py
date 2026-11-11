#!/usr/bin/env python3
"""오도짜세 현황 페이지가 쓰는 게임 이름표(계급·선임·작전·무기·부품)를 게임의 main.gd에서 뽑는다.

  python3 odojjase_names.py <게임 저장소>/main.gd                              이름표(JSON 한 줄)를 찍는다
  python3 odojjase_names.py <게임 저장소>/main.gd odojjase-stats/index.html    페이지의 `const N = …;` 줄을 갈아 끼운다

명부 기록에는 부품·무기가 열쇠(dozer, mortar…)로만 들어 있다 — 부품이나 무기가 늘면 다시 돌린다.
표에 없는 열쇠는 페이지에 열쇠 그대로 보인다.
"""
import json, re, sys


def names(src):
    out = {}
    out["ranks"] = re.findall(r'"([^"]+)"', re.search(r"const RANKS := \[(.*?)\]", src).group(1))
    blk = src[src.index("const SENIORS := ["):]
    blk = blk[:blk.index("\n]\n")]
    out["seniors"] = dict(re.findall(r'"id":\s*"([a-z0-9_]+)".*?"name":\s*"([^"]+)"', blk))
    blk = src[src.index("const OPS := ["):]
    blk = blk[:blk.index("\n]\n")]
    out["ops"] = [[int(r), n] for n, r in re.findall(r'\{"name":\s*"([^"]+)",\s*"risk":\s*(\d+)', blk)]
    blk = src[src.index("const SRC_NAMES := {"):]
    blk = blk[:blk.index("}")]
    out["src"] = dict(re.findall(r'"([a-z0-9_]+)":\s*"([^"]+)"', blk))
    blk = src[src.index("const MODULES := {"):]
    blk = blk[:blk.index("\n}\n")]
    out["mods"] = dict(re.findall(r'^\t"([a-z0-9_]+)":\s*\{[^\n]*?"name":\s*"([^"]+)"', blk, re.M))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    line = json.dumps(names(open(sys.argv[1], encoding="utf-8").read()), ensure_ascii=False, separators=(",", ":"))
    if len(sys.argv) < 3:
        print(line)
        return
    page = open(sys.argv[2], encoding="utf-8").read()
    new, n = re.subn(r"^const N = \{.*\};$", lambda m: "const N = " + line + ";", page, count=1, flags=re.M)
    if n != 1:
        sys.exit("페이지에서 `const N = {…};` 줄을 못 찾았다")
    open(sys.argv[2], "w", encoding="utf-8").write(new)
    print("이름표를 갈아 끼웠다: 부품 %d · 무기 %d" % (len(json.loads(line)["mods"]), len(json.loads(line)["src"])))


if __name__ == "__main__":
    main()
