#!/usr/bin/env python3
"""Cross-reference our Tailwind design tokens against propsmadness.com (the design template).

Fetches their live CSS, resolves their Panda `var()` token graph in dark mode, and
diffs it against the `tailwind.config` embedded in frontend/index.html.

    python3 scripts/verify_design_tokens.py          # report only
    python3 scripts/verify_design_tokens.py --json   # machine-readable

Exits non-zero if anything drifted, so it can gate CI.
"""
import json, re, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
ORIGIN = "https://propsmadness.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}


def fetch(url: str) -> str:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")


def their_css() -> str:
    html = fetch(ORIGIN + "/")
    hrefs = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html)
    out = []
    for h in hrefs:
        if h.startswith("http") and ORIGIN not in h:
            continue  # third-party (e.g. lite-yt-embed)
        out.append(fetch(h if h.startswith("http") else ORIGIN + h))
    if not out:
        sys.exit("could not find any first-party stylesheets; the site layout may have changed")
    return "\n".join(out)


def token_tables(css: str):
    """Return (light_base, dark_overrides) maps of custom property -> raw value."""
    base, dark = {}, {}
    for m in re.finditer(r"([^{}]{0,160})\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        decls = re.findall(r"--([a-z0-9-]+):\s*([^;}]+)", body)
        if not decls:
            continue
        if re.search(r":where\(:root,:host\)", sel):
            base.update(decls)
        elif re.search(r"\.dark", sel):
            dark.update(decls)
    return base, dark


def make_resolver(base, dark):
    def resolve(name, depth=0):
        v = dark.get(name, base.get(name))
        if v is None or depth > 12:
            return None
        v = v.strip()
        m = re.fullmatch(r"var\(--([a-z0-9-]+)\)", v)
        return resolve(m.group(1), depth + 1) if m else v
    return resolve


def our_config() -> dict:
    """eval the runtime tailwind.config out of index.html."""
    src = INDEX_HTML.read_text()
    i = src.index("tailwind.config = ")
    body = src[i + len("tailwind.config = "): src.index("</script>", i)].strip().rstrip(";")
    js = "console.log(JSON.stringify(eval('(' + require('fs').readFileSync(0,'utf8') + ')').theme.extend))"
    r = subprocess.run(["node", "-e", js], input=body, capture_output=True, text=True)
    if r.returncode:
        sys.exit("tailwind.config in index.html did not parse:\n" + r.stderr)
    return json.loads(r.stdout)


def norm_hex(v):
    v = str(v).strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,4})", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    return {"rgb(0 0 0/0)": "#00000000"}.get(v, v)


def norm_num(v):
    v = str(v).strip()
    for suf, mul in (("rem", 16), ("px", 1), ("ms", 1)):
        if v.endswith(suf):
            return round(float(v[: -len(suf)]) * mul, 4)
    if v.endswith("s"):
        return round(float(v[:-1]) * 1000, 4)
    try:
        return round(float(v), 4)
    except ValueError:
        return v


def norm_ease(v):
    return re.sub(r"\s+", "", re.sub(r"\b0\.", ".", str(v)))


def camel_to_kebab(s):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s)
    return re.sub(r"([A-Za-z])([0-9])", r"\1-\2", s).lower()


SCALES = [
    ("radii",       "radii-{}",        "borderRadius",             ["none","xxs","xs","sm","md","lg","xl","2xl","3xl","full"], norm_num),
    ("fontSize",    "font-sizes-{}",   "fontSize",                 ["2xs","xxs","xs","sm","md","lg","xl","2xl","3xl","4xl","5xl","6xl","7xl","8xl","9xl","xxl"], norm_num),
    ("spacing",     "spacing-{}",      "spacing",                  ["xxs","xs","sm","md","lg","xl","xxl","3xl","4xl","5xl"], norm_num),
    ("duration",    "durations-{}",    "transitionDuration",       ["fastest","faster","fast","normal","slow","slower","slowest"], norm_num),
    ("fontWeight",  "font-weights-{}", "fontWeight",               ["regular","semi","strong"], norm_num),
    ("lineHeight",  "line-heights-{}", "lineHeight",               ["none","tight","normal","relaxed","loose"], norm_num),
    ("zIndex",      "z-index-{}",      "zIndex",                   ["hide","base","docked","dropdown","sticky","banner","overlay","modal","popover","toast","tooltip"], norm_num),
]
EASING_MAP = {"standard": "app-standard", "decelerate": "app-decelerate", "emphasized-in": "emphasized-in",
              "emphasized-out": "emphasized-out", "pulse": "pulse", "pm": "default"}


def main():
    as_json = "--json" in sys.argv
    resolve = make_resolver(*token_tables(their_css()))
    cfg = our_config()
    report, drift = {}, 0

    # colors
    ok, bad, absent = 0, [], []
    for name, val in sorted(cfg["colors"].items()):
        theirs = resolve("colors-" + camel_to_kebab(name))
        if theirs is None:
            absent.append(name)
        elif norm_hex(val) == norm_hex(theirs):
            ok += 1
        else:
            bad.append({"token": name, "ours": norm_hex(val), "theirs": norm_hex(theirs)})
    report["colors"] = {"match": ok, "drift": bad, "not_in_their_system": len(absent)}
    drift += len(bad)

    # numeric scales
    for label, tmpl, key, keys, conv in SCALES:
        mine = cfg.get(key, {})
        if key == "fontSize":
            mine = {k: (v[0] if isinstance(v, list) else v) for k, v in mine.items()}
        ok, bad = 0, []
        for k in keys:
            theirs = resolve(tmpl.format(k))
            if theirs is None:
                continue
            if k not in mine:
                bad.append({"token": k, "ours": None, "theirs": theirs})
            elif conv(mine[k]) == conv(theirs):
                ok += 1
            else:
                bad.append({"token": k, "ours": conv(mine[k]), "theirs": conv(theirs)})
        report[label] = {"match": ok, "drift": bad}
        drift += len(bad)

    # easings
    mine_e = cfg.get("transitionTimingFunction", {})
    ok, bad = 0, []
    for ours, theirs_key in EASING_MAP.items():
        theirs = resolve("easings-" + theirs_key)
        if theirs is None:
            continue
        if norm_ease(mine_e.get(ours, "")) == norm_ease(theirs):
            ok += 1
        else:
            bad.append({"token": ours, "ours": mine_e.get(ours), "theirs": theirs})
    report["easings"] = {"match": ok, "drift": bad}
    drift += len(bad)

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for label, r in report.items():
            extra = f"  ({r['not_in_their_system']} of ours not in their system)" if "not_in_their_system" in r else ""
            print(f"{label:11s} {r['match']:3d} match, {len(r['drift']):2d} drift{extra}")
            for d in r["drift"]:
                print(f"    {d['token']}: ours={d['ours']}  theirs={d['theirs']}")
        print("\n" + ("IN SYNC" if not drift else f"{drift} token(s) drifted"))
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
