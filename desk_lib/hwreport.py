"""Hardware test report page (toolkit-free)."""
import html
import time

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def report_html(results, info, app_version):
    """A small standalone HTML page: what was tested, what the pad is, and the result of each step."""
    rows = "".join(
        f"<tr><td>{html.escape(n)}</td><td class='{r.lower()}'>{r}</td><td>{html.escape(d)}</td></tr>" for n, r, d in results)
    n_pass, n_fail = sum(r == PASS for _n, r, _d in results), sum(r == FAIL for _n, r, _d in results)
    head = "ALL PASSED" if n_fail == 0 and n_pass else (f"{n_fail} FAILED" if n_fail else "nothing tested")
    return f"""<!doctype html><meta charset="utf-8"><title>Desk Companion hardware test</title>
<style>body{{font:15px system-ui,sans-serif;margin:40px;color:#111}}h1{{margin:0 0 4px}}table{{border-collapse:collapse;margin-top:18px}}
td,th{{border:1px solid #ccc;padding:8px 14px;text-align:left}}.pass{{color:#15803d;font-weight:700}}.fail{{color:#b91c1c;font-weight:700}}.skip{{color:#888}}
.k{{color:#666}}@media print{{body{{margin:12px}}}}</style>
<h1>Desk Companion - hardware test: {head}</h1>
<div class="k">{time.strftime('%Y-%m-%d %H:%M')} &middot; app {html.escape(app_version)} &middot; firmware {html.escape(str(info.get('fw', '?')))}
&middot; core {html.escape(str(info.get('core', '?')))} &middot; reset {html.escape(str(info.get('reset', '?')))}</div>
<table><tr><th>Check</th><th>Result</th><th>Details</th></tr>{rows}</table>"""
