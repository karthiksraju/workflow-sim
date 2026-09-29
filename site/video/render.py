# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pillow>=11,<13"]
# ///
"""Render the workflow-sim launch video.

The story, keys and values come from src/workflow_sim/examples/billing.py and
its corrected and broken results. Frames are drawn with Pillow at 2x and piped
to ffmpeg as raw RGB. Fonts are the macOS system San Francisco faces.

    uv run --locked --script site/video/render.py              # both formats
    uv run --locked --script site/video/render.py --still 22   # one frame
"""
import argparse
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 30
SS = 2  # supersampling factor
DURATION = 44.0
OUT = Path(__file__).resolve().parent.parent / 'media'

BG = '#f6f7f9'
SURF = '#ffffff'
BORDER = '#e2e5e9'
LINE = '#c3c9d1'
TEXT = '#1a1d21'
SEC = '#5c6570'
FAINT = '#8a93a0'
OK, OK_BG = '#176639', '#d9f2e2'
BAD, BAD_BG = '#8f1d1d', '#fbdcdc'
CODE_BG, CODE_FG, CODE_DIM = '#2d3540', '#e8ebef', '#9aa4b1'

SANS = '/System/Library/Fonts/SFNS.ttf'
MONO = '/System/Library/Fonts/SFNSMono.ttf'

# (start, end, text). Also written to the WebVTT file.
CAPTIONS = [
    (0.4, 3.0, 'A payment webhook sends a receipt.'),
    (3.0, 5.1, 'The receipt is saved. The response is lost.'),
    (5.1, 8.3, 'So the handler retries. One receipt, or two?'),
    (8.9, 11.9, 'Your real handler. A modeled receipt service.'),
    (11.9, 15.0, 'You choose the failure. You write the check.'),
    (15.4, 21.3, 'The bug: each retry gets a new idempotency key.'),
    (21.3, 25.1, 'The check fails. Two receipts were saved.'),
    (25.6, 31.4, 'The fix: reuse the invoice ID as the key.'),
    (31.4, 34.4, 'Same failure, same check. One receipt.'),
    (34.8, 38.6, 'PASS means your checks held in this modeled run.'),
]
END_TRANSCRIPT = ('workflow-sim. Test Python asyncio and Celery workflows under the '
                  'failures you choose. Private alpha. Want to try it on one of your '
                  'workflows? Ask for access: github.com/karthiksraju')

STORY = (0.0, 8.6)
CODE = (8.6, 15.2)
BUG = (15.2, 25.4)
FIX = (25.4, 34.6)
SCOPE = (34.6, 38.8)
END = (38.8, DURATION)
RUN_OFFSET = 1.2  # replay starts this long after the bug/fix scene begins

LAYOUTS = {
    'wide': dict(
        W=1920, H=1080, k=1.0, margin=120,
        mark=(120, 58, 26), cap=(120, 112, 62, 1680), strip=(120, 222, 64, 30),
        hdr=(330, 420), hand=(240, 620), serv=(1000, 1380),
        rows=(510, 585, 715, 790), processed=845, life_end=900,
        receipts=(1460, 330, 1820, 610), card_h=96, check=(1460, 640, 1820, 900),
        code=(320, 44), code_size=33, scope_y=330,
    ),
    'square': dict(
        W=1080, H=1080, k=0.84, margin=64,
        mark=(64, 44, 22), cap=(64, 86, 50, 952), strip=(64, 222, 56, 26),
        hdr=(300, 372), hand=(120, 440), serv=(630, 950),
        rows=(440, 498, 590, 648), processed=692, life_end=720,
        receipts=(64, 760, 524, 1030), card_h=84, check=(556, 760, 1016, 1030),
        code=(290, 36), code_size=22, scope_y=300,
    ),
}

_fonts = {}


def font(size, weight='Regular', mono=False):
    key = (round(size * SS), weight, mono)
    if key not in _fonts:
        f = ImageFont.truetype(MONO if mono else SANS, key[0])
        f.set_variation_by_name(weight)
        _fonts[key] = f
    return _fonts[key]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def prog(t, a, b):
    return ease((t - a) / (b - a)) if b > a else float(t >= a)


def fade(t, a, b, fin=0.4, fout=0.35):
    """Opacity for an element visible from a to b."""
    if t < a or t > b:
        return 0.0
    return min(ease((t - a) / fin), ease((b - t) / fout) if fout else 1.0)


def rgb(c):
    c = c.lstrip('#')
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(back, color, a):
    b, c = rgb(back), rgb(color)
    return tuple(round(b[i] + (c[i] - b[i]) * clamp(a)) for i in range(3))


class Canvas:
    def __init__(self, L):
        self.L = L
        self.k = L['k']
        self.img = Image.new('RGB', (L['W'] * SS, L['H'] * SS), BG)
        self.d = ImageDraw.Draw(self.img)

    def _p(self, *xs):
        return [round(x * SS) for x in xs]

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, r=10, width=2, a=1.0, back=BG):
        self.d.rounded_rectangle(
            self._p(x0, y0, x1, y1), radius=r * SS,
            fill=mix(back, fill, a) if fill else None,
            outline=mix(back, outline, a) if outline else None,
            width=round(width * SS) if outline else 0)

    def text(self, x, y, s, size, color=TEXT, weight='Regular', mono=False, a=1.0,
             back=BG, anchor='la'):
        if a <= 0:
            return
        self.d.text(self._p(x, y), s, font=font(size, weight, mono),
                    fill=mix(back, color, a), anchor=anchor)

    def length(self, s, size, weight='Regular', mono=False):
        return font(size, weight, mono).getlength(s) / SS

    def line(self, x0, y0, x1, y1, color, width=3, a=1.0, back=BG, dash=None):
        if a <= 0:
            return
        fill, w = mix(back, color, a), round(width * SS)
        if not dash:
            self.d.line(self._p(x0, y0, x1, y1), fill=fill, width=w)
            return
        on, off = dash
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        pos = 0.0
        while pos < length:
            e = min(pos + on, length)
            self.d.line(self._p(x0 + (x1 - x0) * pos / length, y0 + (y1 - y0) * pos / length,
                                x0 + (x1 - x0) * e / length, y0 + (y1 - y0) * e / length),
                        fill=fill, width=w)
            pos += on + off

    def head(self, x, y, direction, color, size, a=1.0):
        s = size
        pts = [(x, y), (x - direction * s * 1.3, y - s * 0.75), (x - direction * s * 1.3, y + s * 0.75)]
        self.d.polygon([(round(px * SS), round(py * SS)) for px, py in pts], fill=mix(BG, color, a))

    def pill(self, x, y, s, size, fg, bg, a=1.0, back=SURF):
        w = self.length(s, size, 'Bold') + size * 1.1
        h = size * 1.75
        self.rect(x, y, x + w, y + h, fill=bg, r=h / 2, a=a, back=back)
        self.text(x + w / 2, y + h / 2, s, size, fg, 'Bold', a=a, back=bg, anchor='mm')
        return w

    def wrap(self, s, size, weight, maxw):
        lines, cur = [], ''
        for word in s.split():
            trial = f'{cur} {word}'.strip()
            if cur and self.length(trial, size, weight) > maxw:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        return lines + [cur]

    def frame(self):
        return self.img.reduce(SS)


def draw_chrome(cv, t):
    x, y, size = cv.L['mark']
    a = 1.0 - fade(t, END[0], DURATION + 1, 0.4, 0)
    cv.text(x, y, 'workflow-sim', size, SEC, 'Semibold', a=a)


def draw_caption(cv, t):
    x, y, size, maxw = cv.L['cap']
    for a0, a1, s in CAPTIONS:
        a = fade(t, a0, a1, 0.35, 0.25)
        if a <= 0:
            continue
        dy = (1 - ease((t - a0) / 0.35)) * 14
        for i, line in enumerate(cv.wrap(s, size, 'Semibold', maxw)):
            cv.text(x, y + dy + i * size * 1.2, line, size, TEXT, 'Semibold', a=a)


def arrow(cv, x0, x1, y, p, color, label, a, dashed=False, label_color=None):
    """Horizontal message arrow drawn from x0 toward x1 up to progress p."""
    if p <= 0 or a <= 0:
        return
    k = cv.k
    tip = x0 + (x1 - x0) * p
    direction = 1 if x1 > x0 else -1
    end = tip - direction * 14 * k
    cv.line(x0, y, end, y, color, 3 * k, a=a, dash=(14 * k, 9 * k) if dashed else None)
    cv.head(tip, y, direction, color, 11 * k, a=a)
    if label:
        cv.text((x0 + x1) / 2, y - 14 * k, label, 24 * k, label_color or color,
                'Medium', a=a * clamp(p * 2), anchor='md')


def receipt_card(cv, i, key, a, variant='normal', slide=0.0, highlight=0.0):
    x0, y0, x1, _ = cv.L['receipts']
    k, h = cv.k, cv.L['card_h']
    top = y0 + 52 * k + i * (h + 14 * k) + slide
    fill, outline = SURF, BORDER
    if variant == 'dup':
        fill, outline = BAD_BG, BAD
    if highlight:
        outline = '#%02x%02x%02x' % mix(outline, OK, highlight)
    cv.rect(x0, top, x1, top + h, fill=fill, outline=outline, r=8, width=2, a=a)
    ink = BAD if variant == 'dup' else TEXT
    cv.text(x0 + 22 * k, top + 20 * k, 'in-42 · cus-7 · 1200', 25 * k, ink, 'Medium',
            mono=True, a=a, back=fill)
    cv.text(x0 + 22 * k, top + h - 20 * k, f'key {key}', 21 * k,
            BAD if variant == 'dup' else SEC, a=a, back=fill, anchor='ld')


def placeholder_card(cv, i, a):
    x0, y0, x1, _ = cv.L['receipts']
    k, h = cv.k, cv.L['card_h']
    top = y0 + 52 * k + i * (h + 14 * k)
    for (ax, ay, bx, by) in ((x0, top, x1, top), (x0, top + h, x1, top + h),
                             (x0, top, x0, top + h), (x1, top, x1, top + h)):
        cv.line(ax, ay, bx, by, FAINT, 2, a=a, dash=(10 * k, 8 * k))
    cv.text((x0 + x1) / 2, top + h / 2, '?', 46 * k, SEC, 'Semibold', a=a, anchor='mm')


def diagram(cv, lt, mode, a):
    """Sequence diagram of the billing example. lt is seconds since the replay began."""
    if a <= 0:
        return
    L, k = cv.L, cv.k
    if mode == 'story':
        ev = dict(req=(1.0, 1.8), save=1.9, lost=(3.3, 4.1), wait=(5.3, 6.0),
                  retry=(6.0, 6.8), outcome=7.0, ok=None, done=None)
        keys = ('in-42', 'in-42')
    else:
        ev = dict(req=(0.2, 0.8), save=0.85, lost=(1.0, 1.6), wait=(1.75, 2.35),
                  retry=(2.45, 3.05), outcome=3.1, ok=(3.45, 3.95), done=4.05)
        keys = ('in-42:0', 'in-42:1') if mode == 'bug' else ('in-42', 'in-42')

    hy0, hy1 = L['hdr']
    for (x0, x1), title, sub in ((L['hand'], 'Payment handler', 'your code'),
                                 (L['serv'], 'Receipt service', 'modeled')):
        cv.rect(x0, hy0, x1, hy1, fill=SURF, outline=BORDER, r=10, a=a)
        cx = (x0 + x1) / 2
        cv.text(cx, hy0 + (hy1 - hy0) * 0.42, title, 30 * k, TEXT, 'Semibold', a=a,
                back=SURF, anchor='mm')
        cv.text(cx, hy0 + (hy1 - hy0) * 0.74, sub, 22 * k, SEC, a=a, back=SURF, anchor='mm')
        cv.line(cx, hy1, cx, L['life_end'], LINE, 2, a=a, dash=(8 * k, 8 * k))

    hx = sum(L['hand']) / 2
    sx = sum(L['serv']) / 2
    r1, r2, r3, r4 = L['rows']
    gx = L['margin']
    size = 24 * k

    def when(e, d=0.25):
        return a * clamp((lt - e) / d) if e is not None else 0.0

    # Virtual-time labels in the left gutter.
    cv.text(gx, r1, 't = 0 s', size, SEC, 'Medium', a=when(ev['req'][0]), anchor='lm')
    cv.text(gx, r3, 't = 2 s', size, SEC, 'Medium', a=when(ev['wait'][1]), anchor='lm')

    # First attempt: request, commit, lost response.
    label = 'send receipt' if mode == 'story' else f'send receipt · key {keys[0]}'
    arrow(cv, hx, sx, r1, prog(lt, *ev['req']), TEXT, label, a)
    cv.text(sx + 18 * k, r1 + 4 * k, 'saved', size, SEC, 'Medium', a=when(ev['save']), anchor='lm')
    lp = prog(lt, *ev['lost'])
    if lp > 0:
        stop = sx + (hx - sx) * 0.5 * lp
        cv.line(sx, r2, stop, r2, SEC, 3 * k, a=a, dash=(14 * k, 9 * k))
        xa = when(ev['lost'][1] - 0.05, 0.2)
        s = 13 * k
        cv.line(stop - s, r2 - s, stop + s, r2 + s, BAD, 4 * k, a=xa)
        cv.line(stop - s, r2 + s, stop + s, r2 - s, BAD, 4 * k, a=xa)
        cv.text(stop, r2 + 26 * k, 'response lost', size, BAD, 'Semibold', a=xa, anchor='mt')

    # Virtual wait before the retry.
    wa = when(ev['wait'][0], 0.2)
    if wa > 0:
        mid = (r2 + r3) / 2 + 6 * k
        bx0 = hx + 22 * k
        bw = 150 * k
        cv.text(bx0, mid - 12 * k, 'waits 2 s', 21 * k, SEC, a=wa, anchor='ld')
        cv.rect(bx0, mid, bx0 + bw, mid + 8 * k, fill=BORDER, r=4 * k, a=wa)
        fill_w = bw * prog(lt, *ev['wait'])
        if fill_w > 2:
            cv.rect(bx0, mid, bx0 + fill_w, mid + 8 * k, fill=SEC, r=4 * k, a=wa)

    # Retry and its outcome.
    label = 'retry' if mode == 'story' else f'retry · key {keys[1]}'
    arrow(cv, hx, sx, r3, prog(lt, *ev['retry']), TEXT, label, a)
    oa = when(ev['outcome'])
    if mode == 'bug':
        cv.text(sx + 18 * k, r3 + 4 * k, 'saved again', size, BAD, 'Semibold', a=oa, anchor='lm')
    elif mode == 'fix':
        cv.text(sx + 18 * k, r3 + 4 * k, 'already saved', size, OK, 'Semibold', a=oa, anchor='lm')
    if ev['ok']:
        arrow(cv, sx, hx, r4, prog(lt, *ev['ok']), SEC, 'ok', a, dashed=True)
        cv.text(hx + 18 * k, L['processed'], 'event marked processed', 21 * k, SEC,
                a=when(ev['done']), anchor='lm')

    # Stored receipts, read from the modeled service.
    x0, y0, x1, _ = L['receipts']
    cv.text(x0, y0 + 14 * k, 'Receipts saved', 24 * k, SEC, 'Semibold', a=a, anchor='lm')
    ca = when(ev['save'])
    receipt_card(cv, 0, keys[0], ca, slide=(1 - clamp((lt - ev['save']) / 0.3)) * 10,
                 highlight=(mode == 'fix') * clamp(1 - abs(lt - ev['outcome'] - 0.4) / 0.6))
    if mode == 'story':
        placeholder_card(cv, 1, oa)
    elif mode == 'bug':
        receipt_card(cv, 1, keys[1], oa, 'dup', slide=(1 - clamp((lt - ev['outcome']) / 0.3)) * 10)

    if mode != 'story':
        check_panel(cv, mode, when(5.0, 0.35))


def check_panel(cv, mode, a):
    if a <= 0:
        return
    k = cv.k
    x0, y0, x1, y1 = cv.L['check']
    cv.rect(x0, y0, x1, y1, fill=SURF, outline=BORDER, r=10, a=a)
    px = x0 + 24 * k
    cv.text(px, y0 + 26 * k, 'Check', 21 * k, SEC, a=a, back=SURF)
    cv.text(px, y0 + 56 * k, 'one exact receipt', 28 * k, TEXT, 'Semibold', a=a, back=SURF)
    actual = '2 receipts' if mode == 'bug' else '1 receipt'
    cv.text(px, y0 + 104 * k, 'expected  1 receipt', 23 * k, SEC, mono=True, a=a, back=SURF)
    cv.text(px, y0 + 138 * k, f'actual    {actual}', 23 * k, BAD if mode == 'bug' else OK,
            'Semibold', mono=True, a=a, back=SURF)
    if mode == 'bug':
        cv.pill(px, y1 - 62 * k, 'ASSERTION_FAILED', 22 * k, BAD, BAD_BG, a=a)
    else:
        cv.pill(px, y1 - 62 * k, 'PASS', 22 * k, OK, OK_BG, a=a)


CODE_SERVICE = [
    ('# modeled receipt service (examples/billing.py)', True),
    ('self.receipts.setdefault(key, dict(payload))', False),
    ('if not self.lost_response:', False),
    ('    self.lost_response = True', False),
    ("    raise TimeoutError('receipt committed; acknowledgement lost')", False),
]
CODE_CHECK = [
    ('# your check on the final state', True),
    ("ctx.expect('one exact receipt',", False),
    ('           lambda: list(account.receipts.values()),', False),
    ("           [{'invoice': 'in-42', 'customer': 'cus-7', 'amount': 1200}])", False),
]


def code_height(cv, lines):
    size = cv.L['code_size']
    return size * 2.5 + (len(lines) - 1) * size * 1.55


def code_panel(cv, y0, lines, t_in, t, a):
    L = cv.L
    x0, x1 = L['margin'], L['W'] - L['margin']
    size = L['code_size']
    pa = a * clamp((t - t_in) / 0.35)
    if pa <= 0:
        return
    cv.rect(x0, y0, x1, y0 + code_height(cv, lines), fill=CODE_BG, r=12, a=pa)
    step = size * 1.55
    for i, (s, dim) in enumerate(lines):
        la = pa * clamp((t - t_in - 0.25 - i * 0.18) / 0.3)
        cv.text(x0 + size * 1.1, y0 + size * 1.25 + i * step, s, size,
                CODE_DIM if dim else CODE_FG, mono=True, a=la, back=CODE_BG, anchor='lm')


def code_strip(cv, mode, a):
    if a <= 0:
        return
    x, y, h, size = cv.L['strip']
    tag, line, fg, bg = (('BUG', 'key = f"{invoice[\'id\']}:{attempt}"', BAD, BAD_BG)
                         if mode == 'bug' else ('FIX', "key = invoice['id']", OK, OK_BG))
    w = cv.length(line, size, mono=True) + size * 5.2
    cv.rect(x, y, x + w, y + h, fill=bg, r=10, a=a)
    cv.text(x + size * 0.8, y + h / 2, tag, size * 0.8, fg, 'Bold', a=a, back=bg, anchor='lm')
    cv.text(x + size * 3.4, y + h / 2, line, size, TEXT, 'Medium', mono=True, a=a, back=bg,
            anchor='lm')
    source = 'examples/billing.py, broken=true' if mode == 'bug' else 'examples/billing.py, default'
    cv.text(x + w + size * 0.8, y + h / 2, source, size * 0.72, SEC, a=a, anchor='lm')


SCOPE_CARDS = [
    ('Checked in this run', OK, OK_BG,
     ["Your handler's real retry logic", 'The failure you modeled', 'The final state you checked']),
    ('Not proven by PASS', SEC, '#eef0f3',
     ['Production safety', "The live provider's behavior", 'Every task or thread interleaving']),
]


def scope(cv, t, a):
    if a <= 0:
        return
    L, k = cv.L, cv.k
    x, y = L['margin'], L['scope_y']
    wide = L['W'] > L['H']
    gap = 40 * k
    cw = (L['W'] - 2 * x - gap) / 2 if wide else L['W'] - 2 * x
    ch = 330 * k if wide else 300 * k
    for i, (title, fg, bg, items) in enumerate(SCOPE_CARDS):
        ca = a * clamp((t - SCOPE[0] - 0.5 - i * 0.6) / 0.4)
        cx = x + i * (cw + gap) if wide else x
        cy = y if wide else y + i * (ch + gap)
        cv.rect(cx, cy, cx + cw, cy + ch, fill=SURF, outline=BORDER, r=12, a=ca)
        cv.rect(cx, cy, cx + cw, cy + 70 * k, fill=bg, r=12, a=ca, back=SURF)
        cv.rect(cx, cy + 50 * k, cx + cw, cy + 70 * k, fill=bg, r=0, a=ca, back=SURF)
        cv.text(cx + 32 * k, cy + 35 * k, title, 30 * k, fg, 'Semibold', a=ca, back=bg, anchor='lm')
        for j, item in enumerate(items):
            cv.text(cx + 32 * k, cy + (122 + j * 70) * k, item, 34 * k, TEXT, a=ca, back=SURF,
                    anchor='lm')


def end_card(cv, t, a):
    if a <= 0:
        return
    L, k = cv.L, cv.k
    x = L['margin']
    maxw = L['W'] - 2 * x
    y = L['H'] * (0.22 if L['W'] > L['H'] else 0.16)

    def la(delay):
        return a * clamp((t - END[0] - delay) / 0.4)

    cv.text(x, y, 'workflow-sim', 104 * k, TEXT, 'Bold', a=la(0.1))
    y += 150 * k
    for part in cv.wrap('Test Python asyncio and Celery workflows under the failures you choose.',
                        44 * k, 'Regular', maxw):
        cv.text(x, y, part, 44 * k, TEXT, a=la(0.4))
        y += 56 * k
    y += 28 * k
    w = cv.pill(x, y, 'PRIVATE ALPHA', 22 * k, SEC, '#eef0f3', a=la(0.7), back=BG)
    cv.text(x + w + 18 * k, y + 19 * k, 'v0.1.0a4 · CPython 3.12 · Linux and macOS', 28 * k, SEC,
            a=la(0.7), anchor='lm')
    y += 110 * k
    bw = min(maxw, 900 * k)
    cv.rect(x, y, x + bw, y + 150 * k, fill=SURF, outline=BORDER, r=12, a=la(1.1))
    cv.text(x + 36 * k, y + 50 * k, 'Want to try it on one of your workflows?', 34 * k, TEXT,
            'Semibold', a=la(1.1), back=SURF, anchor='lm')
    cv.text(x + 36 * k, y + 104 * k, 'Ask for access: github.com/karthiksraju', 30 * k, SEC,
            a=la(1.1), back=SURF, anchor='lm')


def render(args):
    t, layout = args
    cv = Canvas(LAYOUTS[layout])
    draw_chrome(cv, t)
    draw_caption(cv, t)
    diagram(cv, t - STORY[0], 'story', fade(t, *STORY))
    y0, gap = cv.L['code']
    code_panel(cv, y0, CODE_SERVICE, CODE[0] + 0.5, t, fade(t, *CODE))
    y0 += code_height(cv, CODE_SERVICE) + gap
    code_panel(cv, y0, CODE_CHECK, CAPTIONS[4][0] + 0.1, t, fade(t, *CODE))
    for mode, (a0, a1) in (('bug', BUG), ('fix', FIX)):
        sa = fade(t, a0, a1)
        code_strip(cv, mode, sa)
        diagram(cv, t - a0 - RUN_OFFSET, mode, sa * clamp((t - a0 - 0.6) / 0.4))
    scope(cv, t, fade(t, *SCOPE))
    end_card(cv, t, fade(t, END[0], DURATION + 1, 0.4, 0))
    return cv.frame().tobytes()


def vtt_time(s):
    return f'{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}'


def write_vtt(path):
    cues = [*CAPTIONS, (END[0] + 0.3, DURATION, END_TRANSCRIPT)]
    body = ['WEBVTT', '']
    for i, (a, b, s) in enumerate(cues, 1):
        body += [str(i), f'{vtt_time(a)} --> {vtt_time(b)}', s, '']
    path.write_text('\n'.join(body))


def encode(layout, path):
    L = LAYOUTS[layout]
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f"{L['W']}x{L['H']}", '-r', str(FPS), '-i', '-',
           '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-tune', 'animation',
           '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
           '-metadata', 'title=workflow-sim: one failure, two outcomes', str(path)]
    frames = [(i / FPS, layout) for i in range(round(DURATION * FPS))]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool() as pool:
        for buf in pool.imap(render, frames, chunksize=8):
            proc.stdin.write(buf)
    proc.stdin.close()
    if proc.wait():
        sys.exit(f'ffmpeg failed for {path}')


def still(t, layout, path):
    L = LAYOUTS[layout]
    Image.frombytes('RGB', (L['W'], L['H']), render((t, layout))).save(path, quality=90)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--still', type=float, help='write one frame at this time instead')
    p.add_argument('--layout', choices=LAYOUTS, help='render one layout only')
    p.add_argument('--out', type=Path, default=OUT)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    layouts = [a.layout] if a.layout else list(LAYOUTS)
    if a.still is not None:
        for layout in layouts:
            still(a.still, layout, a.out / f'still-{layout}-{a.still:05.1f}.png')
        return
    write_vtt(a.out / 'launch.en.vtt')
    still(23.5, 'wide', a.out / 'poster.jpg')
    names = {'wide': 'launch-1920x1080.mp4', 'square': 'launch-1080x1080.mp4'}
    for layout in layouts:
        encode(layout, a.out / names[layout])


if __name__ == '__main__':
    main()
