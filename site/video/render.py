# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pillow>=11,<13"]
# ///
"""Render a workflow-sim walkthrough, grounded in the shipped billing example.

Usage: uv run --locked --script site/video/render.py
Requires macOS system fonts and ffmpeg. Evidence excerpts come from actual
public-runner results; see evidence.json and ../README.md for reproduction.
"""
import argparse
import json
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FPS, SS, DURATION = 30, 2, 64.0
OUT = Path(__file__).resolve().parent.parent / 'media'
EVIDENCE = json.loads(Path(__file__).with_name('evidence.json').read_text())
BG, SURF, BORDER = '#fafaf8', '#ffffff', '#d8dcd4'
TEXT, SEC, FAINT = '#20211f', '#50564c', '#67705f'
OK, OK_BG = '#24704d', '#e9f3ed'
BAD, BAD_BG = '#b73838', '#faeded'
SANS = '/System/Library/Fonts/SFNS.ttf'
MONO = '/System/Library/Fonts/SFNSMono.ttf'
LAYOUTS = {
    'wide': dict(W=1920, H=1080, k=1., margin=120),
    'square': dict(W=1080, H=1080, k=.82, margin=64),
}
SCENES = [0, 6, 15, 27, 37, 46, 56, 64]
TITLES = [
    'The handler returned. What did it write?',
    'Keep your handler. Model the service.',
    'Define the run with workflow-sim.',
    'Replay the failure on virtual time.',
    'Inspect the state your code left behind.',
    'Change your code. Run the same check.',
    'Turn a failure into a repeatable test.',
]
CAPTIONS = [
    (0, 6, 'A handler can return successfully and still save two receipts. Test the resulting state.'),
    (6, 10, 'Call your real workflow code. Replace its external service with a model you write.'),
    (10, 15, 'Here the model saves the receipt, then loses the acknowledgement. The handler retries.'),
    (15, 20, 'Use ctx.at to schedule the payment and a duplicate webhook.'),
    (20, 27, 'Use ctx.expect to check the exact receipt. You define what correct means.'),
    (27, 32, 'Run your adapter with the workflow-sim CLI. Each run starts in a fresh worker process.'),
    (32, 37, 'The virtual clock drives the real retry at two seconds and redelivery at five.'),
    (37, 42, 'The result contains your failed check, expected content and actual content.'),
    (42, 46, 'Expected one exact receipt. Found two. A successful handler was not enough.'),
    (46, 50, 'Fix the key in your handler. Keep the same modeled failure and assertions.'),
    (50, 56, 'Run again. One exact receipt; all four checks match. PASS for this modeled run.'),
    (56, 64, 'You supply the workflow, service models and assertions. workflow-sim controls execution and reports the evidence. Private alpha: github.com/karthiksraju.'),
]
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


def panel(cv, x, y, w, h, title, lines, size=32, highlight=None):
    cv.rect(x, y, x+w, y+h, fill=SURF, outline=BORDER, r=3, width=1)
    cv.text(x+28, y+22, title, 21, SEC, 'Medium')
    cv.line(x, y+68, x+w, y+68, BORDER, width=1)
    for i, line in enumerate(lines):
        yy = y+94+i*size*1.48
        if i == highlight:
            cv.rect(x+18, yy-4, x+w-18, yy+size*1.4, fill=OK_BG, r=0)
        cv.text(x+28, yy, line, size, TEXT, mono=True)


def prose(cv, text, y, size=34, color=SEC):
    x=cv.L['margin'];w=cv.L['W']-2*x
    for i,line in enumerate(cv.wrap(text,size,'Regular',w)):
        cv.text(x,y+i*size*1.3,line,size,color)


def base(cv, scene, t):
    m=cv.L['margin'];w=cv.L['W']-2*m
    cv.text(m,48,'workflow-sim',26,TEXT,'Semibold')
    cv.text(cv.L['W']-m,54,f'{scene+1:02d} / 07',22,SEC,mono=True,anchor='ra')
    cv.line(m,99,m+w,99,BORDER,width=1)
    for i,line in enumerate(cv.wrap(TITLES[scene],54 if w>1000 else 45,'Medium',w)):
        cv.text(m,128+i*61,line,54 if w>1000 else 45,TEXT,'Medium')
    cv.line(m,1010,m+w,1010,BORDER,width=2)
    cv.line(m,1010,m+w*t/DURATION,1010,TEXT,width=3)
    cv.text(m,1031,'REAL EXAMPLE / MODELED SERVICES',17,SEC,mono=True)


def render(args):
    t,layout=args;cv=Canvas(LAYOUTS[layout]);L=cv.L
    m=L['margin'];w=L['W']-2*m;wide=w>1000
    scene=min(6,next((i for i in range(7) if t<SCENES[i+1]),6))
    local=t-SCENES[scene];base(cv,scene,t)
    fs=32 if wide else 25
    if scene==0:
        prose(cv,'A lost acknowledgement triggers a retry.',268)
        y=390;gap=32;cw=(w-gap)/2
        for i,(label,value,fg,bg) in enumerate([
            ('HANDLER','returned',TEXT,SURF),('SAVED RECEIPTS','2',BAD,BAD_BG)]):
            x=m+i*(cw+gap)
            cv.rect(x,y,x+cw,y+260,fill=bg,outline=BORDER,r=3)
            cv.text(x+28,y+30,label,23,SEC,'Medium')
            cv.text(x+28,y+95,value,72,fg,'Medium',a=clamp((local-i*.7)/.5))
        prose(cv,'workflow-sim runs the failure and checks the final state.',735,38,TEXT)
    elif scene==1:
        if local<4:
            gap=34;cw=(w-gap)/2
            panel(cv,m,315,cw,250,'YOUR WORKFLOW',['provision(...)','real Python'],fs)
            panel(cv,m+cw+gap,315,cw,250,'YOUR SERVICE MODEL',['Account()','controlled IO'],fs)
            prose(cv,'Run the handler you already have. Model the boundary it calls.',650,36)
        else:
            lines=['self.receipts.setdefault(key, dict(payload))',
                   'if not self.lost_response:',
                   '    self.lost_response = True',
                   "    raise TimeoutError('receipt committed; acknowledgement lost')"]
            if not wide:lines[-1:] = ['    raise TimeoutError(',"        'receipt committed; acknowledgement lost')"]
            panel(cv,m,300,w,430,'SERVICE MODEL / billing.py excerpt',lines,fs)
            prose(cv,'Save the receipt. Lose the response. Exercise the real retry logic.',790,34)
    elif scene==2:
        lines=['def build(ctx):','    # account and event created above',
               "    ctx.at(0, 'payment',",
               '        lambda: provision(event, account,',
               "            broken=ctx.inputs.get('broken', False)))",
               "    ctx.at(5, 'duplicate-webhook',",
               '        lambda: provision(event, account))']
        if local>=5:
            lines=["ctx.expect('one exact receipt',",
                   '    lambda: list(account.receipts.values()),',
                   "    [{'invoice': 'in-42',",
                   "      'customer': 'cus-7',",
                   "      'amount': 1200}])"]
        panel(cv,m,290,w,500,'ADAPTER EXCERPT / billing.py',lines,fs)
        prose(cv,'You schedule the events.' if local<5 else 'You specify the exact expected state.',835,36,TEXT)
    elif scene==3:
        command=['$ uv run workflow-sim \\',
                 '    workflow_sim.examples.billing:build \\',
                 '    --duration 12 --inputs broken.json \\',
                 '    --output broken-result.json']
        panel(cv,m,280,w,300,'PUBLIC CLI / broken.json = {"broken": true}',command,fs)
        positions=[m+70,m+w*.48,m+w-90]
        yy=710;p=clamp((local-3)/6)
        cv.line(positions[0],yy,positions[-1],yy,BORDER,width=3)
        cv.line(positions[0],yy,positions[0]+(positions[-1]-positions[0])*p,yy,TEXT,width=3)
        for i,(x,at,label) in enumerate(zip(positions,[0,2,5],['payment','retry','redelivery'])):
            active=p>=i/2
            cv.rect(x-7,yy-7,x+7,yy+7,fill=TEXT if active else BORDER,r=7)
            cv.text(x,yy-65,f'{at}s',32,TEXT if active else SEC,'Medium',anchor='ma')
            cv.text(x,yy+28,label,25,SEC,anchor='ma')
        prose(cv,'Fresh worker process. Virtual time. Your real control flow.',850,32)
    elif scene==4:
        check=EVIDENCE['broken']['checks'][1]
        panel(cv,m,280,w,510,'RESULT SUMMARY / broken-result.json',[
            '"outcome": "'+EVIDENCE['broken']['outcome']+'"',
            '"name": "'+check['name']+'"',
            '', 'expected: 1 receipt', 'actual:   2 receipts',
            'invoice in-42 / customer cus-7 / amount 1200'],fs)
        cv.rect(m+18,280+94+4*fs*1.48-4,m+w-18,280+94+5*fs*1.48,fill=BAD_BG,r=0)
        cv.text(m+28,280+94+4*fs*1.48,'actual:   2 receipts',fs,BAD,'Medium',mono=True)
        prose(cv,'Counts summarize the exact payloads in the result.',818,25)
        prose(cv,'The handler returned. The business assertion failed.',882,34,TEXT)
    elif scene==5:
        panel(cv,m,275,w,185,'CHANGE IN YOUR HANDLER',[
            "key = invoice['id']"],38,highlight=0)
        cv.text(m,500,'SAME MODEL + SAME CHECKS / rerun without broken.json',22,SEC,mono=True)
        checks=EVIDENCE['fixed']['checks']
        for i,check in enumerate(checks):
            y=552+i*66;alpha=clamp((local-3-i*.5)/.4)
            cv.line(m,y+51,m+w,y+51,BORDER,width=1)
            cv.text(m,y,check['name'],29 if wide else 24,TEXT,a=alpha)
            cv.text(m+w,y,'MATCH',25,OK,'Medium',mono=True,a=alpha,anchor='ra')
        if local>6:
            cv.rect(m,850,m+w,938,fill=OK_BG,outline=OK,r=3)
            cv.text(m+25,872,EVIDENCE['fixed']['outcome']+' / one exact receipt',36,OK,'Medium')
    else:
        entries=[('YOU PROVIDE','Workflow code · service models · assertions'),
                 ('WORKFLOW-SIM','Controlled execution · virtual time · result evidence')]
        for i,(title,body) in enumerate(entries):
            y=290+i*188
            cv.line(m,y,m+w,y,BORDER,width=1)
            cv.text(m,y+24,title,22,SEC,mono=True)
            for j,line in enumerate(cv.wrap(body,36,'Regular',w)):
                cv.text(m,y+72+j*46,line,36,TEXT)
        prose(cv,'PASS covers this modeled run. Live integrations need their own tests.',704,29)
        cv.text(m,840,'workflow-sim / private alpha',42,TEXT,'Medium')
        cv.text(m,912,'Find me on GitHub: github.com/karthiksraju',29,SEC)
    return cv.frame().tobytes()
def vtt_time(s):
    return f'{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}'


def write_vtt(path):
    cues = CAPTIONS
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
           '-metadata', 'title=workflow-sim: from workflow code to test evidence', str(path)]
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
    still(40, 'wide', a.out / 'poster.jpg')
    names = {'wide': 'launch-1920x1080.mp4', 'square': 'launch-1080x1080.mp4'}
    for layout in layouts:
        encode(layout, a.out / names[layout])


if __name__ == '__main__':
    main()
