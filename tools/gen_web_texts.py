import json, subprocess
d = json.loads(subprocess.check_output(["node", __import__("os").path.join(__import__("os").path.dirname(__file__), "extract_site.js"), __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "site")]))
NAMES = {"app": "Website – menu and shared", "index": "Website – home", "codes": "Website – gift codes", "known-issues": "Website – known issues",
         "report": "Website – report a bug", "changelog": "Website – what's fixed", "appeal": "Website – appeal", "my": "Website – my reports", "follow": "Website – follow a report", "status": "Website – server status"}
out = ['"""Texts of the players\' website (site/*.html, site/app.js), so they show up on the Translations page and can be',
       'translated into every server language. GENERATED from the pages – the pages keep their own English and Polish copy',
       'for offline use, so change a text in both places (tests check they match)."""', "", "WEB_EN = ["]
pl = {}
for page in ["app", "index", "codes", "known-issues", "report", "changelog", "status", "my", "appeal", "follow"]:
    for k, v in d[page]["en"].items():
        out.append(f"    ({('web.' + page + '.' + k)!r}, {NAMES[page]!r}, {v!r}),")
    for k, v in d[page].get("pl", {}).items():
        if k in d[page]["en"]:
            pl[f"web.{page}.{k}"] = v
out += ["]", "", "WEB_PL = {"] + [f"    {k!r}: {v!r}," for k, v in pl.items()] + ["}", ""]
open(__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "texts_web.py"), "w").write("\n".join(out))
print(len(pl))
