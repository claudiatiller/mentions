# update_dashboard.py
# Generate a local index.html dashboard for your daily PDFs.

import sys, pathlib
from datetime import datetime

# ---- CONFIG ----
# Run from the directory that contains your PDF subfolders (e.g., "news instances/"),
# or pass a root path as the first CLI arg:
#   python3 update_dashboard.py "/path/to/news instances"
BASE_DIR = pathlib.Path(".").resolve()
if len(sys.argv) > 1:
    BASE_DIR = pathlib.Path(sys.argv[1]).resolve()

OUTFILE = BASE_DIR / "index.html"
# ----------------


def collect_pdfs(root: pathlib.Path):
    """Return { group_name: [(relpath, filename, mtime), ...] } newest-first."""
    groups = {}
    for sub in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        pdfs = []
        for p in sub.glob("*.pdf"):
            rel = p.relative_to(root).as_posix()
            pdfs.append((rel, p.name, p.stat().st_mtime))
        pdfs.sort(key=lambda t: t[2], reverse=True)
        if pdfs:
            groups[sub.name] = pdfs

    # PDFs directly under BASE_DIR (optional)
    root_pdfs = [(p.name, p.name, p.stat().st_mtime) for p in root.glob("*.pdf")]
    if root_pdfs:
        root_pdfs.sort(key=lambda t: t[2], reverse=True)
        groups["_root"] = root_pdfs

    return groups


def build_html(groups):
    # Years for Year filter (from file mtimes)
    years_present = set()
    for _, pdfs in groups.items():
        for _, _, mtime in pdfs:
            years_present.add(datetime.fromtimestamp(mtime).year)
    years_present = sorted(years_present, reverse=True)

    css = """
    :root{
      --bg:#00bed6;
      --card:#ffffff;
      --ink:#0f172a;
      --muted:#64748b;
      --border:#e5e7eb;
      --pill:#f3f4f6;
      --shadow:0 10px 30px rgba(0,0,0,.12);
      --radius:16px;
    }
    html,body{height:100%}
    body{
      margin:0;
      background:var(--bg);
      font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
      color:var(--ink);
    }
    /* Page fills viewport; header auto height; card fills the rest */
    .page{
      max-width:min(96vw, 1400px);
      margin:0 auto;
      padding:16px;
      min-height:100vh;
      display:grid;
      grid-template-rows:auto 1fr;
      gap:12px;
    }
    .hero{color:#fff}
    .hero h1{margin:0 0 6px;font-size:28px;line-height:1.2}
    .hero .muted{opacity:.9;margin:0}
    /* Card stretches to bottom */
    .card{
      background:var(--card);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      border:1px solid rgba(255,255,255,.25);
      padding:24px;
      min-height:0;
      display:flex;
      flex-direction:column;
    }

    .toolbar{
      display:flex; gap:12px; align-items:end; flex-wrap:wrap;
      margin:4px 0 14px; border-bottom:1px solid var(--border); padding-bottom:12px;
    }
    .field{display:flex;flex-direction:column;gap:6px}
    label{font-size:12px;color:var(--muted)}
    input[type="search"], select, input[type="date"]{
      padding:8px 10px; border:1px solid var(--border); border-radius:10px; background:#fff; color:var(--ink)
    }
    input[type="search"]{width:100%;max-width:320px}
    input[type="date"]{min-width:180px}
    .date-wrap{position:relative;display:flex;align-items:center}
    .date-wrap svg{position:absolute;left:10px;pointer-events:none}
    .date-wrap input{padding-left:34px}

    /* Responsive two-column layout for groups */
    .groups{
      flex:1;               /* make groups fill the card vertically if short */
      display:grid;
      grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
      gap:24px;
      margin-top:12px;
      min-height:0;         /* prevent overflow issues */
    }

    .group{
      background:#fff;
      border:1px solid var(--border);
      border-radius:12px;
      padding:14px 14px 6px;
    }
    .group h2{margin:6px 4px 10px;font-size:18px}

    .pdf-item{margin:10px 0}
    details{border:1px solid var(--border);border-radius:12px;padding:12px;background:#fff}
    summary{cursor:pointer;font-weight:600}
    .meta{color:var(--muted);font-size:12px;margin-left:6px}
    .pill{background:var(--pill);border:1px solid var(--border);border-radius:999px;padding:2px 8px;font-size:12px;color:#555;margin-left:6px}
    iframe{width:100%; height:80vh; border:none; border-radius:10px; background:#fafafa}
    .hidden{display:none}
    """

    js = """
    const q = document.getElementById('q');
    const fromDate = document.getElementById('fromDate');
    const toDate = document.getElementById('toDate');
    const yearSel = document.getElementById('year');
    const monthSel = document.getElementById('month');
    const weekdaySel = document.getElementById('weekday');
    const sortSel = document.getElementById('sort');

    function withinRange(dateStr, fromStr, toStr) {
      if (!fromStr && !toStr) return true;
      if (fromStr && dateStr < fromStr) return false;
      if (toStr && dateStr > toStr) return false;
      return true;
    }

    function applyFilters() {
      const term = (q.value || "").toLowerCase();
      const y = yearSel.value;
      const m = monthSel.value;
      const wd = weekdaySel.value;
      const fromStr = fromDate.value || "";
      const toStr = toDate.value || "";

      const items = Array.from(document.querySelectorAll('[data-item]'));
      items.forEach(el => {
        const name = el.getAttribute('data-name');
        const itemY = el.getAttribute('data-year');
        const itemM = el.getAttribute('data-month');
        const itemWD = el.getAttribute('data-weekday');
        const itemDate = el.getAttribute('data-date');

        const okTerm  = !term || name.includes(term);
        const okYear  = !y || itemY === y;
        const okMonth = !m || itemM === m;
        const okWD    = !wd || itemWD === wd;
        const okRange = withinRange(itemDate, fromStr, toStr);

        const show = okTerm && okYear && okMonth && okWD && okRange;
        el.classList.toggle('hidden', !show);
      });

      // Sort visible items within each group
      document.querySelectorAll('[data-group]').forEach(group => {
        const children = Array.from(group.querySelectorAll('[data-item]')).filter(el => !el.classList.contains('hidden'));
        children.sort((a,b) => {
          const ma = parseFloat(a.getAttribute('data-mtime'));
          const mb = parseFloat(b.getAttribute('data-mtime'));
          return sortSel.value === 'new' ? mb - ma : ma - mb;
        });
        children.forEach(ch => group.appendChild(ch));
      });
    }

    [q, fromDate, toDate, yearSel, monthSel, weekdaySel, sortSel].forEach(el => {
      el.addEventListener('input', applyFilters);
      el.addEventListener('change', applyFilters);
    });

    window.addEventListener('DOMContentLoaded', applyFilters);
    """

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [f"""<!doctype html>
<html><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>News PDFs — Local Index</title>
<style>{css}</style>
</head><body>
  <div class="page">
    <header class="hero">
      <h1>News PDFs — Local Index</h1>
      <p class="muted">Generated {now}. Open this file directly in your browser; no server required.</p>
    </header>

    <div class="card">
      <div class="toolbar">
        <div class="field">
          <label for="q">Search</label>
          <input id="q" type="search" placeholder="Filename/date… (e.g. Tuesday or 23-09-2025)"/>
        </div>

        <div class="field">
          <label for="fromDate">From date</label>
          <div class="date-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="#6b7280" d="M7 2h2v2h6V2h2v2h3v18H4V4h3V2zm13 6H6v12h14V8zM8 12h4v4H8v-4z"/></svg>
            <input id="fromDate" type="date" />
          </div>
        </div>

        <div class="field">
          <label for="toDate">To date</label>
          <div class="date-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="#6b7280" d="M7 2h2v2h6V2h2v2h3v18H4V4h3V2zm13 6H6v12h14V8zM8 12h4v4H8v-4z"/></svg>
            <input id="toDate" type="date" />
          </div>
        </div>

        <div class="field">
          <label for="year">Year</label>
          <select id="year">
            <option value="">All years</option>"""]

    for y in years_present:
        parts.append(f'<option value="{y}">{y}</option>')

    parts.append("""          </select>
        </div>

        <div class="field">
          <label for="month">Month</label>
          <select id="month">
            <option value="">All months</option>
            <option value="01">January</option>
            <option value="02">February</option>
            <option value="03">March</option>
            <option value="04">April</option>
            <option value="05">May</option>
            <option value="06">June</option>
            <option value="07">July</option>
            <option value="08">August</option>
            <option value="09">September</option>
            <option value="10">October</option>
            <option value="11">November</option>
            <option value="12">December</option>
          </select>
        </div>

        <div class="field">
          <label for="weekday">Weekday</label>
          <select id="weekday">
            <option value="">All days</option>
            <option>Monday</option>
            <option>Tuesday</option>
            <option>Wednesday</option>
            <option>Thursday</option>
            <option>Friday</option>
            <option>Saturday</option>
            <option>Sunday</option>
          </select>
        </div>

        <div class="field">
          <label for="sort">Sort</label>
          <select id="sort">
            <option value="new" selected>Newest first</option>
            <option value="old">Oldest first</option>
          </select>
        </div>
      </div>

      <div class="groups">""")

    # --- custom display names + left-to-right order ---
    DISPLAY_NAME_MAP = {
        "Party Leaders": "UK Party Leaders",
        "_root": "Loose PDFs",
    }
    preferred_order = ["Reform MPs", "Party Leaders"]  # left → right

    ordered_keys = [k for k in preferred_order if k in groups]
    ordered_keys += [k for k in sorted(groups.keys(), key=str.lower)
                     if k not in ordered_keys and k != "_root"]
    if "_root" in groups:
        ordered_keys.append("_root")

    # Render sections in that order
    for group_name in ordered_keys:
        pdfs = groups[group_name]
        title = DISPLAY_NAME_MAP.get(group_name, group_name)
        parts.append(f'<section class="group"><h2>{title}</h2><div data-group>')
        for rel, fname, mtime in pdfs:
            dt = datetime.fromtimestamp(mtime)
            meta = dt.strftime("%Y-%m-%d %H:%M")
            data_name = f"{title} {fname} {meta}".lower()
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
            weekday = dt.strftime("%A")
            date_str = dt.strftime("%Y-%m-%d")

            parts.append(f"""
  <div class="pdf-item" data-item
       data-name="{data_name}"
       data-date="{date_str}"
       data-year="{year}"
       data-month="{month}"
       data-weekday="{weekday}"
       data-mtime="{mtime}">
    <details>
      <summary>{fname}
        <span class="pill">{weekday}</span>
      </summary>
      <p><a href="{rel}" target="_blank">Open in new tab</a></p>
      <iframe src="{rel}"></iframe>
    </details>
  </div>""")
        parts.append("</div></section>")

    parts.append("""      </div> <!-- /.groups -->
    </div> <!-- /.card -->
  </div> <!-- /.page -->
  <script>""" + js + """</script>
</body></html>
""")
    return "".join(parts)


def main():
    if not BASE_DIR.exists():
        raise SystemExit(f"Base dir not found: {BASE_DIR}")
    groups = collect_pdfs(BASE_DIR)
    html = build_html(groups)
    OUTFILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTFILE}")


if __name__ == "__main__":
    main()
