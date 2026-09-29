# Landing page alternatives

Three candidate landing pages for workflow-sim, for the owner to compare
before one replaces `site/index.html`. Each is a single static HTML file with inline CSS
and JavaScript. There's no build step, and nothing loads from another origin.

| Path | What it is |
| --- | --- |
| `index.html` | Chooser. Live, scaled previews of the three pages, plus links. |
| `signal/` | Dark developer-tool launch. The animated two-lane scope is drawn in SVG by the inline script. |
| `editorial/` | Warm, serif, asymmetric. Two illustrated run "receipts" in HTML/CSS; one button swaps which is in front. |
| `playground/` | Demo-led. Toggle the key, scrub virtual time, see receipts and checks change. |
| `assets/fonts/` | Bundled OFL fonts, with their licences: Instrument Serif (Editorial) and Recursive (Playground). Signal uses system fonts. |

Preview with `cd site && python3 -m http.server 8000`, then open
<http://localhost:8000/alternatives/>. Media is shared with the current page through
`../../media/`.

## What each page claims

All three tell the billing example from `src/workflow_sim/examples/billing.py`:
invoice `in-42`, customer `cus-7`, amount `1200`, receipt saved, acknowledgement lost,
retry at 2 virtual seconds. A new key per attempt (`in-42:0`, `in-42:1`) saves two
receipts and fails `one exact receipt` (ASSERTION_FAILED). The stable invoice ID
keeps one receipt (PASS). Playground also shows the redelivered webhook at 5 s and all
four `ctx.expect` checks. If that example changes, update the keys, times and check
names in all three pages.

The animations are illustrations, and each page says so. None of them runs Python.

## Constraints kept on purpose

- The CTA goes to the owner's GitHub profile, labeled "Private alpha · Find me on
  GitHub". There's no signup form. Replace it everywhere when a real access route exists.
- No install commands or repository links, because the repository is private.
- Scope lives in one footer line: v0.1.0a4, CPython 3.12, Linux/macOS, Celery as the
  only queue backend, and "PASS means your checks held in a modeled run".
- No testimonials, logos or usage numbers.

## Fonts

Fonts were subset to Latin with `pyftsubset` (fonttools) and converted to WOFF2.
Recursive was also pinned to `slnt=0`, `CRSV=0.5` with `fonttools varLib.instancer`,
keeping the `wght`, `CASL` and `MONO` axes. Both are SIL OFL 1.1; the licence files sit
next to the fonts and must ship with them.
