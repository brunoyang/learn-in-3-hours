#!/usr/bin/env python3
"""Build a course library: one index page for every course in a folder.

Usage:  python3 build_library.py <library_dir> [--stage <out_dir>]

Scans   <library_dir>/<course>/_work/course.json   (one level deep; skips _* and .* folders)
        <library_dir>/<course>/_work/timing.json   (minutes, written by build_course.py)
Writes  <library_dir>/index.html                   (course list with per-device progress)

--stage <out_dir> also assembles a publishable bundle for reading on a phone:
        <out_dir>/page.html      the index without <!DOCTYPE>/<html>/<head>/<body>, for hosts
                                 that wrap the page in their own skeleton (Claude Artifacts)
        <out_dir>/library.html   the index as a full document
        <out_dir>/<course>/...   built pages, each with a "全部课程" link to ../library.html,
                                 plus the local files they link to; code/text files (lab/*.sql,
                                 *.py ...) become <file>.html viewer pages so they stay UTF-8
        <out_dir>/files.json     {published path: source} for the host's supporting-files map

Progress comes from the same localStorage key the course pages write (l3h:<slug>), so the
index shows it only where the pages and the index share an origin, and only per browser.
"""
import html
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_course import THEMES  # noqa: E402

DEFAULT_ACCENT = ("#2563eb", "#7aa7ff")  # course.css "ocean"
TEXT_TYPES = {".sql", ".py", ".txt", ".md", ".csv", ".json", ".r", ".sh", ".js", ".ts"}
MAX_LINKED_BYTES = 5 * 1024 * 1024
LIB_LINK = '<a class="lib" href="../library.html">全部课程</a>'


def esc(s):
    return html.escape(str(s), quote=True)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_courses(lib):
    out = []
    for name in sorted(os.listdir(lib)):
        d = os.path.join(lib, name)
        if name.startswith((".", "_")) or not os.path.isdir(d):
            continue
        course = load_json(os.path.join(d, "_work", "course.json"))
        if not course or not course.get("pages"):
            continue
        pages = course["pages"]
        entry = next((p["id"] for p in pages if p.get("type") == "map"), pages[0]["id"])
        if not os.path.exists(os.path.join(d, entry + ".html")):
            print("skip %s: not built yet (run build_course.py)" % name, file=sys.stderr)
            continue
        timing = load_json(os.path.join(d, "_work", "timing.json"), {})
        theme = THEMES.get(course.get("theme") or "ocean")
        accent = (theme[0][0], theme[1][0]) if theme else DEFAULT_ACCENT
        built = max(os.path.getmtime(os.path.join(d, p["id"] + ".html"))
                    for p in pages if os.path.exists(os.path.join(d, p["id"] + ".html")))
        out.append({
            "dir": name,
            "slug": course.get("slug") or name,
            "title": course.get("title", name),
            "target": course.get("target", ""),
            "acts": course.get("acts") or [],
            "pages": [{"id": p["id"], "title": p.get("title", p["id"])} for p in pages],
            "entry": entry,
            "core": timing.get("total_core_minutes"),
            "deep": timing.get("total_deep_minutes"),
            "built": time.strftime("%Y-%m-%d", time.localtime(built)),
            "accent": accent,
        })
    return out


CSS = """
:root{
  --bg:#f6f7f9;--card:#ffffff;--ink:#1a1d23;--ink2:#454c59;--muted:#737b8a;--line:#e2e5ea;
  --track:#e9ecf1;--accent:#2563eb;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0f1115;--card:#171a21;--ink:#e8eaee;--ink2:#bcc2cc;--muted:#8a91a0;--line:#2a2f3a;
  --track:#252a34;--accent:#7aa7ff;color-scheme:dark}
  :root:not([data-theme="light"]) .course{--ca:var(--ca-d)}}
:root[data-theme="dark"]{
  --bg:#0f1115;--card:#171a21;--ink:#e8eaee;--ink2:#bcc2cc;--muted:#8a91a0;--line:#2a2f3a;
  --track:#252a34;--accent:#7aa7ff;color-scheme:dark}
:root[data-theme="dark"] .course{--ca:var(--ca-d)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC","Noto Sans SC","Source Han Sans SC",sans-serif}
.lib-wrap{max-width:760px;margin:0 auto;padding-inline:16px;padding-block:28px 40px}
.lib-head h1{font-size:26px;line-height:1.3;margin:0;text-wrap:balance}
.lib-head p{margin:4px 0 0;color:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}
.courses{list-style:none;margin:20px 0 0;padding:0;display:grid;gap:14px}
.course{--ca:var(--ca-l);background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0;overflow-wrap:anywhere}
.course h2{margin:0;font-size:19px;line-height:1.4;text-wrap:balance}
.course h2 a{color:var(--ink);text-decoration:none}
.course h2 a:hover{color:var(--ca)}
.acts{margin:2px 0 0;font-size:13px;color:var(--ca);font-weight:600}
.target{margin:8px 0 0;color:var(--ink2);font-size:15px;line-height:1.65}
.target b{color:var(--ink);font-weight:600}
.meta{margin:8px 0 0;font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.prog{display:flex;align-items:center;gap:10px;margin-top:12px}
.track{flex:1;height:6px;border-radius:3px;background:var(--track);overflow:hidden}
.track i{display:block;height:100%;width:0;background:var(--ca)}
.count{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.go{display:inline-block;margin-top:10px;padding:8px 14px;border-radius:8px;border:1px solid var(--ca);
  color:var(--ca);text-decoration:none;font-size:15px;font-weight:600;max-width:100%}
.go:hover{background:var(--track)}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.foot{margin-top:22px;font-size:13px;color:var(--muted)}
"""

JS = """
(function(){[].forEach.call(document.querySelectorAll(".course"),function(el){
var ids=JSON.parse(el.getAttribute("data-ids")),titles=JSON.parse(el.getAttribute("data-titles")),d={};
try{d=JSON.parse(localStorage.getItem("l3h:"+el.getAttribute("data-slug"))||"{}")||{}}catch(e){}
var n=0,next=-1;ids.forEach(function(id,i){if(d[id])n++;else if(next<0)next=i;});
el.querySelector(".track i").style.width=(100*n/ids.length)+"%";
el.querySelector(".count").textContent="已学 "+n+" / "+ids.length+" 页";
var go=el.querySelector(".go"),base=el.getAttribute("data-dir")+"/";
if(n===0)return;
if(next<0){go.textContent="已全部学完 · 回到学习地图";return;}
go.href=base+ids[next]+".html";go.textContent="继续："+titles[next]+" →";});})();
"""


TEXT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>:root{{--bg:#f6f7f9;--ink:#1a1d23;--muted:#737b8a;--line:#e2e5ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--ink:#e8eaee;--muted:#8a91a0;--line:#2a2f3a;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--ink)}}
h1{{margin:0;padding:12px 16px;font:600 14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);border-bottom:1px solid var(--line)}}
pre{{margin:0;padding:12px 16px 32px;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}</style>
</head><body><h1>{name}</h1><pre>{body}</pre></body></html>
"""


def render(courses):
    items = []
    for c in courses:
        mins = "核心约 %d 分钟" % c["core"] if c["core"] else ""
        if mins and c["deep"]:
            mins += " · 选读 +%d" % c["deep"]
        meta = " · ".join(x for x in ("%d 页" % len(c["pages"]), mins, "更新于 " + c["built"]) if x)
        href = "%s/%s.html" % (c["dir"], c["entry"])
        items.append(
            '<li class="course" style="--ca-l:{al};--ca-d:{ad}" data-dir="{dir}" data-slug="{slug}" data-ids="{ids}" data-titles="{titles}">'
            '<h2><a href="{href}">{title}</a></h2>'
            '{acts}'
            '<p class="target"><b>学完能做到：</b>{target}</p>'
            '<p class="meta">{meta}</p>'
            '<div class="prog"><div class="track"><i></i></div><span class="count">已学 0 / {n} 页</span></div>'
            '<a class="go" href="{href}">从学习地图开始 →</a>'
            '</li>'.format(
                al=c["accent"][0], ad=c["accent"][1], dir=esc(c["dir"]), slug=esc(c["slug"]),
                ids=esc(json.dumps([p["id"] for p in c["pages"]])),
                titles=esc(json.dumps([p["title"] for p in c["pages"]], ensure_ascii=False)),
                href=esc(href), title=esc(c["title"]),
                acts=('<p class="acts">%s</p>' % " → ".join(esc(a) for a in c["acts"])) if c["acts"] else "",
                target=esc(c["target"]), meta=esc(meta), n=len(c["pages"])))
    total_pages = sum(len(c["pages"]) for c in courses)
    total_min = sum(c["core"] or 0 for c in courses)
    summary = "%d 门课 · %d 页 · 核心约 %.1f 小时" % (len(courses), total_pages, total_min / 60.0)
    body = ('<main class="lib-wrap"><header class="lib-head"><h1>三小时课程库</h1><p>%s</p></header>'
            '<ul class="courses">%s</ul>'
            '<p class="foot">进度来自每页底部的“标记为已学完”，记在当前浏览器里：电脑和手机各记各的。</p></main>'
            % (summary, "".join(items)))
    fragment = "<title>三小时课程库</title>\n<style>%s</style>\n%s\n<script>%s</script>\n" % (CSS, body, JS)
    full = ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<title>三小时课程库</title>\n<style>%s</style></head>\n<body>\n%s\n<script>%s</script>\n</body></html>\n'
            % (CSS, body, JS))
    return fragment, full


def stage(lib, courses, fragment, full, out):
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    files = {}
    with open(os.path.join(out, "page.html"), "w", encoding="utf-8") as f:
        f.write(fragment)
    with open(os.path.join(out, "library.html"), "w", encoding="utf-8") as f:
        f.write(full)
    files["library.html"] = os.path.join(out, "library.html")
    for c in courses:
        src_dir = os.path.join(lib, c["dir"])
        dst_dir = os.path.join(out, c["dir"])
        os.makedirs(dst_dir)
        docs, linked = {}, set()
        for p in c["pages"]:
            src = os.path.join(src_dir, p["id"] + ".html")
            if not os.path.exists(src):
                continue
            doc = open(src, encoding="utf-8").read()
            docs[p["id"]] = doc.replace('<div class="top-in">', '<div class="top-in">' + LIB_LINK, 1)
            for href in re.findall(r'href="([^"#?:]+)"', doc):
                if not href.endswith(".html") and not href.startswith("/"):
                    linked.add(href)
        # Hosts may serve text/plain without a charset, which garbles Chinese comments on
        # phones; wrap code and data files in a UTF-8 page and point the links there instead.
        renamed = {}
        for href in sorted(linked):
            src = os.path.normpath(os.path.join(src_dir, href))
            if not src.startswith(os.path.abspath(src_dir) + os.sep) or not os.path.isfile(src):
                print("warning: %s links to %s, which does not exist" % (c["dir"], href), file=sys.stderr)
                continue
            if os.path.getsize(src) > MAX_LINKED_BYTES:
                print("warning: %s/%s is over 5 MB, not staged" % (c["dir"], href), file=sys.stderr)
                continue
            if os.path.splitext(href)[1].lower() in TEXT_TYPES:
                renamed[href] = href + ".html"
                dst = os.path.join(dst_dir, renamed[href])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(TEXT_PAGE.format(name=esc(href), body=esc(open(src, encoding="utf-8").read())))
            else:
                dst = os.path.join(dst_dir, href)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            files["%s/%s" % (c["dir"], renamed.get(href, href))] = dst
        for pid, doc in docs.items():
            for old, new in renamed.items():
                doc = doc.replace('href="%s"' % old, 'href="%s"' % new)
            dst = os.path.join(dst_dir, pid + ".html")
            with open(dst, "w", encoding="utf-8") as f:
                f.write(doc)
            files["%s/%s.html" % (c["dir"], pid)] = dst
    with open(os.path.join(out, "files.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=1)
    return files


def main(argv):
    if len(argv) not in (2, 4) or (len(argv) == 4 and argv[2] != "--stage"):
        print(__doc__)
        return 2
    lib = os.path.abspath(argv[1])
    courses = find_courses(lib)
    if not courses:
        print("no built courses found under %s" % lib, file=sys.stderr)
        return 1
    fragment, full = render(courses)
    with open(os.path.join(lib, "index.html"), "w", encoding="utf-8") as f:
        f.write(full)
    print("library: %d courses → %s" % (len(courses), os.path.join(lib, "index.html")))
    for c in courses:
        print("  %-24s %3d pages  core %s min" % (c["dir"], len(c["pages"]), c["core"] or "?"))
    if len(argv) == 4:
        out = os.path.abspath(argv[3])
        files = stage(lib, courses, fragment, full, out)
        print("staged %d files → %s (main page: page.html, supporting files: files.json)" % (len(files), out))
        url = (load_json(os.path.join(lib, "library.json"), {}) or {}).get("artifact_url")
        print("publish to the existing URL: %s" % url if url else
              "first publish: record the URL as artifact_url in %s" % os.path.join(lib, "library.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
