#!/usr/bin/env python
"""Build the MOZA R3 guide (v2, slow pace, minimal copy): cut the footage windows, read the
game's own input log, write index.html and compositions/*.html.

    python build.py            # footage cuts (if missing) + html
    python build.py --cuts     # redo the footage cuts too

The generated html is overwritten by every build: change the video here, not in Studio.

Everything on the wheel is placed from the 1000 px MOZA photo (WHEEL below) so a label sits on
its real button; the numbers that drive the wheel, the pedals and the button flashes come from
promo/footage/<clip>.json, written by tools/shoot.py while the game ran.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOOT = HERE.parent / "footage"
FPS = 30
NL = chr(10)

# ---------------------------------------------------------------- footage windows
#: name -> (source clip, start s, length s). Every window is cut to assets/footage/<name>.mp4.
SEG = {
    "hook":   ("steer_para", 0.5, 8.0),
    "menu":   ("menu",       0.2, 12.5),
    "pedals": ("pedals_t1",  3.0, 15.0),
    "steer":  ("steer_run",  8.0, 13.0),
    "camA":   ("cams",       0.5, 7.0),
    "backA":  ("lookback",   0.5, 7.0),
    "resetA": ("reset",      1.5, 7.5),
    "pauseA": ("pause",      0.8, 8.0),
    "shiftA": ("shift",      0.2, 7.0),
}
BTN = {"A": 0, "B": 1, "X": 2, "Y": 3, "UP": 4, "RIGHT": 5, "DOWN": 6, "LEFT": 7,
       "DOWNSHIFT": 12, "UPSHIFT": 13, "CAM": 21, "HOME": 24}


def cut_footage(force: bool):
    out = HERE / "assets" / "footage"
    out.mkdir(parents=True, exist_ok=True)
    for name, (src, t0, dur) in SEG.items():
        dst = out / f"{name}.mp4"
        if dst.exists() and not force:
            continue
        print("cut", name)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0}",
                        "-i", str(FOOT / f"{src}.mp4"), "-t", f"{dur}", "-an", "-r", str(FPS),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-g", "30",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)], check=True)


_logs: dict = {}


def log_of(clip: str) -> list:
    if clip not in _logs:
        _logs[clip] = json.loads((FOOT / f"{clip}.json").read_text())["log"]
    return _logs[clip]


def series(seg: str, key: str, hz: int = 15, smooth: int = 5) -> list:
    """The game's own value of *key* over a footage window, *hz* samples a second
    (a moving average over *smooth* frames takes the autopilot's jitter off a wheel)."""
    src, t0, dur = SEG[seg]
    log = log_of(src)
    vals = [r[key] for r in log]
    out = []
    for k in range(int(round(dur * hz)) + 1):
        i = min(len(vals) - 1, int(round((t0 + k / hz) * FPS)))
        lo, hi = max(0, i - smooth // 2), min(len(vals), i + smooth // 2 + 1)
        out.append(round(sum(vals[lo:hi]) / (hi - lo), 3))
    return out


def presses(seg: str) -> list:
    """[(button, seconds into the window)] for every press of a wheel button."""
    src, t0, dur = SEG[seg]
    ev, prev = [], 0
    for r in log_of(src):
        b = r["btn"]
        for name, i in BTN.items():
            if b >> i & 1 and not prev >> i & 1 and t0 - 0.05 <= r["t"] < t0 + dur:
                ev.append((name, round(max(0.0, r["t"] - t0), 2)))
        prev = b
    return ev


# ---------------------------------------------------------------- the product photos
#: Button centres on the 1000 px ES wheel photo (x, y); rects are (x0, y0, x1, y1).
WHEEL = dict(A=(684, 563), B=(718, 530), X=(649, 530), Y=(684, 497),
             CAM=(284, 626), DPAD=(310, 530), LED=(498, 411))
WHEEL_RECT = dict(PADL=(176, 364, 262, 708), PADR=(735, 364, 826, 708),
                  DPAD=(262, 482, 358, 578), LED=(418, 402, 580, 424))
CROP = (55, 92)          # wheel.png is the photo cropped here
WHEEL_PX = (883, 836)    # wheel.png size
PEDAL_CROP = (70, 144)
PEDAL_PX = (854, 714)
PAD_L = [(495, 147), (590, 156), (601, 164), (592, 245), (562, 340), (550, 385),
         (447, 376), (435, 350), (452, 250), (460, 217)]
PAD_R = [(750, 172), (852, 180), (865, 192), (860, 245), (840, 310), (812, 420),
         (702, 412), (687, 395), (700, 300), (707, 250), (720, 200)]


def wp(name, s):
    x, y = WHEEL[name]
    return ((x - CROP[0]) * s, (y - CROP[1]) * s)


def wrect(name, s):
    x0, y0, x1, y1 = WHEEL_RECT[name]
    return ((x0 - CROP[0]) * s, (y0 - CROP[1]) * s, (x1 - x0) * s, (y1 - y0) * s)


def circle_css(name, s, d=76):
    """A ring around a round button, d px across on the 760 px wheel (scaled with s)."""
    x, y = wp(name, s)
    r = d * s / 0.86 / 2
    return f"left:{x - r:.1f}px;top:{y - r:.1f}px;width:{2 * r:.1f}px;height:{2 * r:.1f}px;"


def rect_css(name, s, pad=0):
    x, y, w, h = wrect(name, s)
    return f"left:{x - pad:.1f}px;top:{y - pad:.1f}px;width:{w + 2 * pad:.1f}px;height:{h + 2 * pad:.1f}px;"


def pad_box(pts, sc):
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    ox, oy = (x0 - PEDAL_CROP[0]) * sc, (y0 - PEDAL_CROP[1]) * sc
    poly = "polygon(" + ",".join(f"{(x - PEDAL_CROP[0]) * sc - ox:.1f}px {(y - PEDAL_CROP[1]) * sc - oy:.1f}px"
                                 for x, y in pts) + ")"
    return ox, oy, (x1 - x0) * sc, (y1 - y0) * sc, poly


def pad_svg(pts, sc, ident, stroke="#f0d430", fill="none", fop=0.3, sw=5, opacity=None):
    p = " ".join(f"{(x - PEDAL_CROP[0]) * sc:.1f},{(y - PEDAL_CROP[1]) * sc:.1f}" for x, y in pts)
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<polygon id="{ident}" points="{p}" fill="{fill}" fill-opacity="{fop}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linejoin="round"{op}/>')


# ---------------------------------------------------------------- timeline
SCENES = [("s1", 8.0), ("s2", 12.5), ("s3", 15.0), ("s4", 13.0), ("s5", 42.5), ("s6", 14.0)]
T, _t = {}, 0.0
for _n, _d in SCENES:
    T[_n] = (_t, _t + _d)
    _t += _d
TOTAL = _t
SEAMS = [T[n][0] for n, _ in SCENES[1:]]
D = {n: b - a for n, (a, b) in T.items()}

CSS = """
@font-face{font-family:"Bahn";src:local("Bahnschrift"),local("Bahnschrift Regular");font-weight:100 900}
@font-face{font-family:"NotoKR";src:url("assets/fonts/NotoSansKR-VF.ttf") format("truetype");font-weight:100 900}
#root{position:absolute;inset:0;overflow:hidden;background:#15151e;color:#fff;font-family:"NotoKR",sans-serif}
#root *{margin:0;padding:0;box-sizing:border-box}
.lat{font-family:"Bahn","NotoKR",sans-serif;font-weight:700}
.abs{position:absolute}
.vid{position:absolute;left:0;top:0;width:1920px;height:1080px;object-fit:cover}
.w{display:inline-block;white-space:pre}
.h{font-weight:900;letter-spacing:-0.02em;line-height:1.14;white-space:nowrap}
.sub{font-weight:700;color:#969aa2;line-height:1.3;white-space:nowrap}
.chip{position:absolute;background:#f0d430;color:#15151e;font-weight:900;text-align:center;
  clip-path:polygon(16px 0,100% 0,calc(100% - 16px) 100%,0 100%);white-space:nowrap}
.chip.red{background:#e10600;color:#fff}.chip.grn{background:#3cd25a;color:#15151e}
.chip.dim{background:#2a2a38;color:#969aa2}
.card{position:absolute;background:#1f1f2b;border-top:8px solid #e10600}
.ring{position:absolute;border:5px solid #f0d430;border-radius:50%;opacity:0}
.ring.sq{border-radius:14px}
.fl{position:absolute;background:#f0d430;border-radius:50%;opacity:0}
.fl.sq{border-radius:10px}
.pn{position:absolute;left:0;top:0;height:1080px;background:#15151e}
.pnr{position:absolute;left:0;top:0;height:1080px;background:#e10600}
"""
SLASH = "clip-path:polygon(0 0,100% 0,calc(100% - 100px) 100%,0 100%)"


def tpl(text: str, **kw) -> str:
    """Fill $name$ tokens (keeps JS braces readable)."""
    for k, v in kw.items():
        text = text.replace(f"${k}$", str(v))
    return text


def scene_html(cid: str, body: str, js: str, extra_css: str = "") -> str:
    return tpl("""<template>
  <style>$css$</style>
  <div id="root" data-composition-id="$cid$" data-width="1920" data-height="1080">
$body$
  </div>
  <script>
    const tl = gsap.timeline({ paused: true });
    const WORDS = (sel, t, dur = 0.2, gap = 0.14) =>
      tl.fromTo(sel, { opacity: 0, filter: "blur(6px)" },
        { opacity: 1, filter: "blur(0px)", duration: dur, stagger: gap, ease: "power2.out" }, t);
    const RAMP = (sel, prop, arr, t0, dt, extra = {}) => {
      for (let i = 0; i < arr.length - 1; i++)
        tl.fromTo(sel, { [prop]: arr[i], ...extra }, { [prop]: arr[i + 1], duration: dt, ease: "none",
          immediateRender: i === 0, ...extra }, t0 + i * dt);
    };
$js$
    window.__timelines["$cid$"] = tl;
  </script>
</template>
""", css=CSS + extra_css, cid=cid, body=body, js=js)


def words(text: str, cls: str = "w") -> str:
    parts = text.split(" ")
    out = []
    for i, w in enumerate(parts):
        out.append(f'<span class="{cls}">{w}</span>')
        if i < len(parts) - 1:
            out.append('<span class="w"> </span>')
    return "".join(out)


def clip_attrs(start, dur, idx=0):
    return f'data-start="{start}" data-duration="{dur}" data-track-index="{idx}"'


# ================================================================ S1  hook
def s1():
    body = f"""
    <div id="s1-vw" class="abs" style="left:0;top:0;width:1920px;height:1080px">
      <video id="s1-vid" class="clip vid" src="assets/footage/hook.mp4" muted playsinline {clip_attrs(0, D["s1"])}></video></div>
    <div id="s1-pnr" class="pnr" style="width:1040px;{SLASH}"></div>
    <div id="s1-pn" class="pn" style="width:1020px;{SLASH}"></div>
    <img id="s1-logo" class="abs" src="assets/img/fai_logo.png" style="left:96px;top:236px;width:620px"/>
    <div id="s1-t2" class="abs h" style="left:88px;top:470px;font-size:200px;line-height:1.1">{words("조작법", "w s1m")}</div>
    <div id="s1-t3" class="abs lat" style="left:100px;top:760px;font-size:84px;letter-spacing:0.06em;color:#969aa2">MOZA R3</div>
    <div id="s1-card" class="card" style="left:1130px;top:150px;width:694px;height:520px"></div>
    <img id="s1-bundle" class="abs" src="assets/img/bundle.png" style="left:1160px;top:202px;width:634px"/>
"""
    js = """
    tl.fromTo("#s1-vw", { x: 0 }, { x: 560, duration: 0.9, ease: "power4.out" }, 1.2);
    tl.fromTo("#s1-pnr", { x: -1300 }, { x: 0, duration: 0.9, ease: "power4.out" }, 1.2);
    tl.fromTo("#s1-pn", { x: -1300 }, { x: 0, duration: 0.9, ease: "power4.out" }, 1.3);
    tl.fromTo("#s1-logo", { opacity: 0, x: -60, filter: "blur(8px)" },
      { opacity: 1, x: 0, filter: "blur(0px)", duration: 0.9, ease: "power4.out" }, 2.1);
    WORDS("#s1-t2 .s1m", 3.0, 0.3, 0.3);
    tl.fromTo("#s1-t3", { opacity: 0, filter: "blur(8px)" }, { opacity: 1, filter: "blur(0px)", duration: 0.6, ease: "power2.out" }, 4.2);
    tl.fromTo("#s1-card", { x: 300, opacity: 0 }, { x: 0, opacity: 1, duration: 1.0, ease: "power4.out" }, 4.6);
    tl.fromTo("#s1-bundle", { x: 300, opacity: 0, filter: "blur(10px)" },
      { x: 0, opacity: 1, filter: "blur(0px)", duration: 1.0, ease: "power4.out" }, 4.75);
"""
    return scene_html("s1", body, js)


# ================================================================ S2  start (menu)
def s2():
    S = 0.86
    ev = presses("menu")
    arms = {"UP": (310, 506), "DOWN": (310, 554), "LEFT": (286, 530), "RIGHT": (334, 530)}
    flashes, fjs = [], []
    for k, (name, t) in enumerate(ev):
        if name in arms:
            ax, ay = arms[name]
            x, y = (ax - CROP[0]) * S, (ay - CROP[1]) * S
            flashes.append(f'<div id="s2-f{k}" class="fl sq" style="left:{x - 17:.1f}px;top:{y - 17:.1f}px;width:34px;height:34px"></div>')
        else:
            flashes.append(f'<div id="s2-f{k}" class="fl" style="{circle_css(name, S)}"></div>')
        fjs.append(f'tl.fromTo("#s2-f{k}", {{opacity:0}}, {{opacity:0.85, duration:0.1, yoyo:true, repeat:1, ease:"none"}}, {t + 0.05:.2f});')
    rows = [("십자키", 236, 52, "선택", 1.0, "#s2-rd"), ("A", 236, 60, "결정", 5.0, "#s2-ra"),
            ("B", 236, 60, "뒤로", 9.0, "#s2-rb")]
    row_html, row_js = [], []
    for k, (key, cw, fs, label, t, ring) in enumerate(rows):
        y = 700 + k * 104
        row_html.append(f'<div id="s2-c{k}" class="chip lat" style="left:944px;top:{y}px;width:{cw}px;height:88px;line-height:88px;font-size:{fs}px">{key}</div>'
                        f'<div id="s2-l{k}" class="abs h" style="left:{944 + cw + 34}px;top:{y - 2}px;font-size:72px;line-height:92px">{words(label, f"w s2l{k}")}</div>')
        row_js.append(f'tl.fromTo("#s2-c{k}", {{opacity:0, x:40}}, {{opacity:1, x:0, duration:0.7, ease:"power4.out"}}, {t});')
        row_js.append(f'WORDS("#s2-l{k} .s2l{k}", {t + 0.35}, 0.3);')
        row_js.append(f'tl.fromTo("{ring}", {{opacity:0}}, {{opacity:1, duration:0.4}}, {t + 0.1});')
    body = f"""
    <div id="s2-h" class="abs h" style="left:96px;top:44px;font-size:76px">{words("시작", "w s2h")}</div>
    <div class="card" style="left:96px;top:150px;width:800px;height:810px"></div>
    <div id="s2-wheel" class="abs" style="left:116px;top:200px;width:{883 * S:.0f}px;height:{836 * S:.0f}px">
      <img src="assets/img/wheel.png" style="position:absolute;left:0;top:0;width:100%;height:100%"/>
      <div id="s2-rd" class="ring sq" style="{rect_css('DPAD', S, 6)}"></div>
      <div id="s2-ra" class="ring" style="{circle_css('A', S)}"></div>
      <div id="s2-rb" class="ring" style="{circle_css('B', S)}"></div>
      {''.join(flashes)}
    </div>
    <div class="abs" style="left:944px;top:142px;width:880px;height:8px;background:#e10600"></div>
    <video id="s2-vid" class="clip abs" src="assets/footage/menu.mp4" muted playsinline
      style="left:944px;top:150px;width:880px;height:495px;object-fit:cover" {clip_attrs(0, D["s2"])}></video>
    {''.join(row_html)}
"""
    js = f"""
    WORDS("#s2-h .s2h", 0.6, 0.3);
    tl.fromTo("#s2-wheel", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.9, ease: "power4.out" }}, 0.5);
    tl.fromTo("#s2-vid", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, ease: "power2.out" }}, 0.6);
    {NL.join(row_js)}
    {NL.join(fjs)}
"""
    return scene_html("s2", body, js)


# ================================================================ S3  pedals
def s3():
    d = D["s3"]
    sc = 540 / PEDAL_PX[0]
    ix, iy = 96, 380
    thr, brk = series("pedals", "thr"), series("pedals", "brk")
    dt = 1 / 15
    lx, ly, lw, lh, lpoly = pad_box(PAD_L, sc)
    rx, ry, rw, rh, rpoly = pad_box(PAD_R, sc)
    t_brk = next(k * dt for k, v in enumerate(brk) if v > 0.2)
    t_thr = 1.8
    body = f"""
    <video id="s3-vid" class="clip vid" src="assets/footage/pedals.mp4" muted playsinline {clip_attrs(0, d)}></video>
    <div id="s3-pnr" class="pnr" style="width:740px;{SLASH}"></div>
    <div id="s3-pn" class="pn" style="width:720px;{SLASH}"></div>
    <div id="s3-h" class="abs h" style="left:96px;top:84px;font-size:104px">{words("페달", "w s3h")}</div>
    <div id="s3-pedals" class="abs" style="left:{ix}px;top:{iy}px;width:540px;height:{PEDAL_PX[1] * sc:.0f}px">
      <img src="assets/img/pedals.png" style="position:absolute;left:0;top:0;width:100%;height:100%"/>
      <div style="position:absolute;left:{lx:.1f}px;top:{ly:.1f}px;width:{lw:.1f}px;height:{lh:.1f}px;clip-path:{lpoly}">
        <div id="s3-fl" style="position:absolute;left:0;top:0;width:100%;height:100%;background:#e10600;opacity:.85"></div></div>
      <div style="position:absolute;left:{rx:.1f}px;top:{ry:.1f}px;width:{rw:.1f}px;height:{rh:.1f}px;clip-path:{rpoly}">
        <div id="s3-fr" style="position:absolute;left:0;top:0;width:100%;height:100%;background:#3cd25a;opacity:.85"></div></div>
      <svg style="position:absolute;left:0;top:0;width:100%;height:100%;overflow:visible" viewBox="0 0 540 {PEDAL_PX[1] * sc:.0f}">
        {pad_svg(PAD_L, sc, "s3-ol", opacity=0)}{pad_svg(PAD_R, sc, "s3-or", opacity=0)}</svg>
    </div>
    <div id="s3-cl" class="chip red lat" style="left:{ix + lx - 50:.0f}px;top:{iy + ly - 78:.0f}px;width:210px;height:62px;line-height:62px;font-size:40px;opacity:0">브레이크</div>
    <div id="s3-cr" class="chip grn lat" style="left:{ix + rx + 10:.0f}px;top:{iy + ry - 78:.0f}px;width:140px;height:62px;line-height:62px;font-size:40px;opacity:0">가속</div>
    <div id="s3-pr" class="abs lat" style="left:96px;top:880px;font-size:60px;opacity:0"><span style="color:#3cd25a">가속</span> <span class="pct" id="s3-nt"></span></div>
    <div id="s3-pl" class="abs lat" style="left:96px;top:956px;font-size:60px;opacity:0"><span style="color:#e10600">브레이크</span> <span class="pct" id="s3-nb"></span></div>
"""
    css = """
    @property --n { syntax: "<integer>"; inherits: false; initial-value: 0; }
    .pct { counter-reset: n var(--n); }
    .pct::after { content: counter(n) "%"; }
    """
    js = tpl("""
    gsap.set("#s3-fl,#s3-fr", { scaleY: 0, transformOrigin: "50% 100%" });
    tl.fromTo("#s3-pn", { x: -760 }, { x: 0, duration: 0.8, ease: "power4.out" }, 0.5);
    tl.fromTo("#s3-pnr", { x: -760 }, { x: 0, duration: 0.8, ease: "power4.out" }, 0.45);
    WORDS("#s3-h .s3h", 1.2, 0.3);
    tl.fromTo("#s3-pedals", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.9, ease: "power4.out" }, 1.3);
    tl.fromTo("#s3-or", { opacity: 0 }, { opacity: 1, duration: 0.4 }, $thr$);
    tl.fromTo("#s3-cr", { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.6, ease: "power4.out" }, $thr$ + 0.1);
    tl.fromTo("#s3-pr", { opacity: 0 }, { opacity: 1, duration: 0.5 }, $thr$ + 0.2);
    tl.fromTo("#s3-ol", { opacity: 0 }, { opacity: 1, duration: 0.4 }, $tb$ - 0.8);
    tl.fromTo("#s3-cl", { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.6, ease: "power4.out" }, $tb$ - 0.7);
    tl.fromTo("#s3-pl", { opacity: 0 }, { opacity: 1, duration: 0.5 }, $tb$ - 0.6);
    RAMP("#s3-fr", "scaleY", $thr_a$, 0, $dt$);
    RAMP("#s3-fl", "scaleY", $brk_a$, 0, $dt$);
    $thr_n$.forEach((v, k) => tl.set("#s3-nt", { "--n": v }, k * $dt$));
    $brk_n$.forEach((v, k) => tl.set("#s3-nb", { "--n": v }, k * $dt$));
""", thr=t_thr, tb=f"{t_brk:.2f}", thr_a=json.dumps(thr), brk_a=json.dumps(brk), dt=f"{dt:.4f}",
        thr_n=json.dumps([round(v * 100) for v in thr]), brk_n=json.dumps([round(v * 100) for v in brk]))
    return scene_html("s3", body, js, css)


# ================================================================ S4  wheel
def s4():
    d = D["s4"]
    s = 540 / WHEEL_PX[0]
    st = series("steer", "steer")
    dt = 1 / 15
    ANG = 110
    deg = [round(v * ANG, 1) for v in st]

    def lit(sign):
        on, out = False, []
        for v in st:
            x = v * sign
            on = x > 0.22 if not on else x > 0.1
            out.append(1 if on else 0)
        return out
    lr, ll = lit(1), lit(-1)
    body = f"""
    <video id="s4-vid" class="clip vid" src="assets/footage/steer.mp4" muted playsinline {clip_attrs(0, d)}></video>
    <div id="s4-pnr" class="pnr" style="width:740px;{SLASH}"></div>
    <div id="s4-pn" class="pn" style="width:720px;{SLASH}"></div>
    <div id="s4-h" class="abs h" style="left:96px;top:84px;font-size:104px">{words("휠", "w s4h")} <span class="w s4h" style="color:#f0d430">조향</span></div>
    <img id="s4-wheel" class="abs" src="assets/img/wheel.png" style="left:96px;top:380px;width:540px;height:{WHEEL_PX[1] * s:.0f}px"/>
    <div id="s4-l" class="chip lat" style="left:96px;top:940px;width:240px;height:72px;line-height:72px;font-size:44px;background:#2a2a38;color:#969aa2">◀ 왼쪽</div>
    <div id="s4-r" class="chip lat" style="left:356px;top:940px;width:240px;height:72px;line-height:72px;font-size:44px;background:#2a2a38;color:#969aa2">오른쪽 ▶</div>
    <div id="s4-ll" class="chip lat" style="left:96px;top:940px;width:240px;height:72px;line-height:72px;font-size:44px;opacity:0">◀ 왼쪽</div>
    <div id="s4-rl" class="chip lat" style="left:356px;top:940px;width:240px;height:72px;line-height:72px;font-size:44px;opacity:0">오른쪽 ▶</div>
"""
    js = tpl("""
    tl.fromTo("#s4-pn", { x: -760 }, { x: 0, duration: 0.8, ease: "power4.out" }, 0.5);
    tl.fromTo("#s4-pnr", { x: -760 }, { x: 0, duration: 0.8, ease: "power4.out" }, 0.45);
    WORDS("#s4-h .s4h", 1.2, 0.3, 0.3);
    tl.fromTo("#s4-wheel", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.9, ease: "power4.out" }, 1.4);
    tl.fromTo("#s4-l,#s4-r", { opacity: 0 }, { opacity: 1, duration: 0.6 }, 2.0);
    RAMP("#s4-wheel", "rotation", $deg$, 0, $dt$, { transformOrigin: "50% 50%" });
    RAMP("#s4-rl", "opacity", $lr$, 0, $dt$);
    RAMP("#s4-ll", "opacity", $ll$, 0, $dt$);
""", deg=json.dumps(deg), lr=json.dumps(lr), ll=json.dumps(ll), dt=f"{dt:.4f}")
    return scene_html("s4", body, js)


# ================================================================ S5  six buttons
STEPS = [
    dict(tab="CAM", key="CAM", chipw=210, title="카메라", sub="", tag="", seg="camA", dur=7.0, rings=["CAM"]),
    dict(tab="A", key="A", chipw=140, title="뒤 보기", sub="다시 누르면 앞", tag="", seg="backA", dur=7.0, rings=["A"]),
    dict(tab="CAM+A", key="CAM + A", chipw=320, title="코스 복귀", sub="", tag="", seg="resetA", dur=7.5, rings=["CAM", "A"]),
    dict(tab="X", key="X", chipw=140, title="일시정지", sub="십자키로 선택", tag="", seg="pauseA", dur=8.0, rings=["X"]),
    dict(tab="패들", key="패들", chipw=210, title="변속", sub="오른쪽 업 · 왼쪽 다운", tag="설정에서 켜기", seg="shiftA", dur=7.0, rings=["PADL", "PADR"]),
    dict(tab="Y", key="Y", chipw=140, title="DRS", sub="깜빡일 때", tag="설정에서 켜기", seg=None, dur=6.0, rings=["Y"]),
]
FLASH_FOR = {"CAM": "CAM", "A": "A", "X": "X", "Y": "Y", "UP": "DPAD", "DOWN": "DPAD",
             "UPSHIFT": "PADR", "DOWNSHIFT": "PADL"}


def s5():
    d = D["s5"]
    S = 0.86
    starts, t = [], 0.0
    for st in STEPS:
        starts.append(t)
        t += st["dur"]
    assert abs(t - d) < 1e-6, (t, d)
    tabw = 142
    tabs = "".join(
        f'<div id="s5-tab{k}" class="abs lat" style="left:{944 + k * (tabw + 5.6):.1f}px;top:60px;width:{tabw}px;height:54px;'
        f'line-height:54px;text-align:center;font-size:32px;background:#1f1f2b;color:#969aa2;'
        f'clip-path:polygon(12px 0,100% 0,calc(100% - 12px) 100%,0 100%)">{st["tab"]}</div>'
        for k, st in enumerate(STEPS))
    rings = []
    for name in ("CAM", "A", "X", "Y"):
        rings.append(f'<div id="s5-r{name}" class="ring" style="{circle_css(name, S)}"></div>')
    for name in ("PADL", "PADR"):
        rings.append(f'<div id="s5-r{name}" class="ring sq" style="{rect_css(name, S, 4)};border-radius:40px"></div>')
    fl_html, fl_js, vids, stills, blocks, text_js, tab_js = [], [], [], [], [], [], []
    seen = set()
    for k, st in enumerate(STEPS):
        t0, dur = starts[k], st["dur"]
        if st["seg"]:
            vids.append(f'<video id="s5-v{k}" class="clip abs" src="assets/footage/{st["seg"]}.mp4" muted playsinline '
                        f'style="left:944px;top:150px;width:880px;height:495px;object-fit:cover" {clip_attrs(t0, dur)}></video>')
            for j, (name, pt) in enumerate(presses(st["seg"])):
                tgt = FLASH_FOR.get(name)
                if tgt is None or pt >= dur - 0.2:
                    continue
                if tgt == "DPAD":
                    x, y, w, h = wrect("DPAD", S)
                    fl_html.append(f'<div id="s5-f{k}_{j}" class="fl sq" style="left:{x:.1f}px;top:{y:.1f}px;width:{w:.1f}px;height:{h:.1f}px"></div>')
                elif tgt in ("PADL", "PADR"):
                    fl_html.append(f'<div id="s5-f{k}_{j}" class="fl sq" style="{rect_css(tgt, S, 4)};border-radius:40px"></div>')
                else:
                    fl_html.append(f'<div id="s5-f{k}_{j}" class="fl" style="{circle_css(tgt, S)}"></div>')
                fl_js.append(f'tl.fromTo("#s5-f{k}_{j}", {{opacity:0}}, {{opacity:0.8, duration:0.1, yoyo:true, repeat:1, ease:"none"}}, {t0 + pt + 0.05:.2f});')
        else:
            stills.append(f'<div class="abs" style="left:944px;top:150px;width:880px;height:495px;overflow:hidden">'
                          f'<img id="s5-v{k}a" class="clip abs" src="assets/img/drs_ready.png" style="left:0;top:0;width:880px;height:495px;object-fit:cover" {clip_attrs(t0, dur, 0)}/>'
                          f'<img id="s5-v{k}b" class="clip abs" src="assets/img/drs_open.png" style="left:0;top:0;width:880px;height:495px;object-fit:cover;opacity:0" {clip_attrs(t0, dur, 1)}/></div>')
            fl_js.append(f'tl.fromTo("#s5-v{k}a,#s5-v{k}b", {{scale:1, y:0, transformOrigin:"50% 50%"}}, {{scale:1.9, y:-320, transformOrigin:"50% 50%", duration:1.4, ease:"power2.inOut"}}, {t0 + 0.6:.2f});')
            fl_html.append(f'<div id="s5-f{k}_0" class="fl" style="{circle_css("Y", S)}"></div>')
            fl_js.append(f'tl.fromTo("#s5-f{k}_0", {{opacity:0}}, {{opacity:0.8, duration:0.1, yoyo:true, repeat:1, ease:"none"}}, {t0 + 3.2:.2f});')
            fl_js.append(f'tl.fromTo("#s5-v{k}b", {{opacity:0}}, {{opacity:1, duration:0.2}}, {t0 + 3.3:.2f});')
        sub = f'<div class="abs sub" style="left:0;top:140px;font-size:50px">{st["sub"]}</div>' if st["sub"] else ""
        tag = (f'<div class="chip dim lat" style="left:0;top:{214 if st["sub"] else 140}px;width:240px;height:58px;'
               f'line-height:58px;font-size:38px">{st["tag"]}</div>') if st["tag"] else ""
        blocks.append(f"""
    <div id="s5-st{k}" class="abs" style="left:944px;top:690px;width:880px;height:320px;opacity:0">
      <div class="chip lat" style="left:0;top:0;width:{st["chipw"]}px;height:116px;line-height:116px;font-size:{64 if len(st["key"]) < 6 else 56}px">{st["key"]}</div>
      <div class="abs h" style="left:{st["chipw"] + 40}px;top:0;font-size:96px;line-height:116px">{st["title"]}</div>
      {sub}{tag}
    </div>""")
        text_js.append(f'tl.fromTo("#s5-st{k}", {{opacity:0, x:30, filter:"blur(8px)"}}, {{opacity:1, x:0, filter:"blur(0px)", duration:0.7, ease:"power4.out"}}, {t0 + 0.4:.2f});')
        if k < len(STEPS) - 1:
            text_js.append(f'tl.to("#s5-st{k}", {{opacity:0, filter:"blur(8px)", duration:0.3, ease:"power2.in"}}, {t0 + dur - 0.4:.2f});')
        for rn in st["rings"]:
            ir = "" if rn not in seen else ", immediateRender:false"
            seen.add(rn)
            text_js.append(f'tl.fromTo("#s5-r{rn}", {{opacity:0}}, {{opacity:1, duration:0.4{ir}}}, {t0 + 0.2:.2f});')
            if k < len(STEPS) - 1:
                text_js.append(f'tl.to("#s5-r{rn}", {{opacity:0, duration:0.3}}, {t0 + dur - 0.3:.2f});')
        tab_js.append(f'tl.to("#s5-tab{k}", {{backgroundColor:"#f0d430", color:"#15151e", duration:0.3}}, {t0 + 0.1:.2f});')
        if k < len(STEPS) - 1:
            tab_js.append(f'tl.to("#s5-tab{k}", {{backgroundColor:"#1f1f2b", color:"#969aa2", duration:0.3}}, {t0 + dur - 0.2:.2f});')
    body = f"""
    <div id="s5-h" class="abs h" style="left:96px;top:44px;font-size:76px">{words("버튼", "w s5h")}</div>
    {tabs}
    <div class="card" style="left:96px;top:150px;width:800px;height:810px"></div>
    <div id="s5-wheel" class="abs" style="left:116px;top:200px;width:{883 * S:.0f}px;height:{836 * S:.0f}px">
      <img src="assets/img/wheel.png" style="position:absolute;left:0;top:0;width:100%;height:100%"/>
      {''.join(rings)}
      {''.join(fl_html)}
    </div>
    <div class="abs" style="left:944px;top:142px;width:880px;height:8px;background:#e10600"></div>
    {''.join(vids)}
    {''.join(stills)}
    {''.join(blocks)}
"""
    js = f"""
    WORDS("#s5-h .s5h", 0.6, 0.3);
    tl.fromTo("#s5-wheel", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.9, ease: "power4.out" }}, 0.5);
    tl.fromTo("[id^=s5-tab]", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, stagger: 0.08 }}, 0.6);
    {NL.join(text_js)}
    {NL.join(tab_js)}
    {NL.join(fl_js)}
"""
    return scene_html("s5", body, js)


# ================================================================ S6  cheat sheet
def s6():
    s = 640 / WHEEL_PX[0]
    wx, wy = 640, 228
    P = lambda name: (wx + wp(name, s)[0], wy + wp(name, s)[1])
    padl = (wx + (232 - CROP[0]) * s, wy + (400 - CROP[1]) * s)
    padr = (wx + (782 - CROP[0]) * s, wy + (400 - CROP[1]) * s)
    LX, RX = 560, 1360
    left = [("LED", "엔진 회전수", 190, P("LED")), ("왼쪽 패들", "변속 다운 *", 330, padl),
            ("십자키", "메뉴 이동", 470, P("DPAD")), ("CAM", "카메라 · +A 코스 복귀", 610, P("CAM"))]
    labels, lines = [], []
    for k, (a, b, y, tgt) in enumerate(left):
        labels.append(f'<div id="s6-L{k}" class="abs" style="left:96px;top:{y}px;width:{LX - 96}px;height:110px;text-align:right;opacity:0">'
                      f'<div class="lat" style="font-size:50px;color:#f0d430;line-height:58px;white-space:nowrap">{a}</div>'
                      f'<div class="abs" style="right:0;top:60px;font-size:40px;font-weight:700;color:#fff;white-space:nowrap">{b}</div></div>')
        lines.append((f"s6-ln-L{k}", (LX + 14, y + 29), tgt))
    labels.append(f'<div id="s6-R0" class="abs" style="left:{RX}px;top:190px;height:110px;opacity:0">'
                  f'<div class="lat" style="font-size:50px;color:#f0d430;line-height:58px;white-space:nowrap">오른쪽 패들</div>'
                  f'<div class="abs" style="left:0;top:60px;font-size:40px;font-weight:700;color:#fff;white-space:nowrap">변속 업 *</div></div>')
    lines.append(("s6-ln-R0", (RX - 14, 190 + 29), padr))
    rows = [("Y", "DRS *"), ("X", "일시정지"), ("B", "핸드브레이크"), ("A", "뒤 보기")]
    for k, (key, a) in enumerate(rows):
        y = 326 + k * 88
        labels.append(f'<div id="s6-K{k}" class="abs" style="left:{RX}px;top:{y}px;width:464px;height:76px;opacity:0">'
                      f'<div class="chip lat" style="left:0;top:0;width:76px;height:76px;line-height:76px;font-size:46px">{key}</div>'
                      f'<div class="abs h" style="left:108px;top:0;font-size:52px;line-height:76px">{a}</div></div>')
    rings = "".join(f'<div id="s6-ring{n}" class="ring" style="{circle_css(n, s)}"></div>' for n in ("A", "B", "X", "Y", "CAM"))
    rings += f'<div id="s6-ringD" class="ring sq" style="{rect_css("DPAD", s, 6)}"></div>'
    rings += "".join(f'<div id="s6-ring{n}" class="ring sq" style="{rect_css(n, s, 4)};border-radius:40px"></div>' for n in ("PADL", "PADR"))
    rings += f'<div id="s6-ringLED" class="ring sq" style="{rect_css("LED", s, 10)};border-radius:10px"></div>'
    ps = 310 / PEDAL_PX[0]
    px_, py_ = 1450, 768
    lx, ly, lw, lh, _ = pad_box(PAD_L, ps)
    rx, ry, rw, rh, _ = pad_box(PAD_R, ps)
    svg_lines = []
    for ident, (x0, y0), (x1, y1) in lines:
        svg_lines.append(f'<line id="{ident}b" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="#15151e" stroke-width="8" stroke-linecap="round" opacity="0"/>')
        svg_lines.append(f'<line id="{ident}" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity="0"/>')
    body = f"""
    <div id="s6-h" class="abs h" style="left:96px;top:44px;font-size:76px">{words("한눈에 보기", "w s6h")}</div>
    <div id="s6-wheel" class="abs" style="left:{wx}px;top:{wy}px;width:{883 * s:.0f}px;height:{836 * s:.0f}px;opacity:0">
      <img src="assets/img/wheel.png" style="position:absolute;left:0;top:0;width:100%;height:100%"/>
      {rings}
    </div>
    <svg id="s6-lines" class="abs" style="left:0;top:0;width:1920px;height:1080px" viewBox="0 0 1920 1080">{''.join(svg_lines)}</svg>
    {''.join(labels)}
    <div id="s6-steer" class="abs h" style="left:{wx - 60}px;top:{wy + 836 * s + 34:.0f}px;width:{883 * s + 120:.0f}px;text-align:center;font-size:56px;opacity:0"><span style="color:#f0d430">휠</span> 조향</div>
    <div id="s6-pedals" class="abs" style="left:{px_}px;top:{py_}px;width:310px;height:{PEDAL_PX[1] * ps:.0f}px;opacity:0">
      <img src="assets/img/pedals.png" style="position:absolute;left:0;top:0;width:100%;height:100%"/>
      <svg style="position:absolute;left:0;top:0;width:100%;height:100%;overflow:visible" viewBox="0 0 310 {PEDAL_PX[1] * ps:.0f}">
        {pad_svg(PAD_L, ps, "s6-pl", "#e10600", "#e10600", 0.3)}{pad_svg(PAD_R, ps, "s6-pr", "#3cd25a", "#3cd25a", 0.3)}</svg>
    </div>
    <div id="s6-cb" class="chip red lat" style="left:{px_ + lx - 90:.0f}px;top:{py_ - 68}px;width:180px;height:56px;line-height:56px;font-size:34px;opacity:0">브레이크</div>
    <div id="s6-cg" class="chip grn lat" style="left:{px_ + rx - 10:.0f}px;top:{py_ - 68}px;width:120px;height:56px;line-height:56px;font-size:34px;opacity:0">가속</div>
    <div id="s6-foot" class="abs sub" style="left:96px;top:976px;font-size:40px;opacity:0">* 설정에서 켜기</div>
"""
    js = """
    WORDS("#s6-h .s6h", 0.6, 0.3, 0.3);
    tl.fromTo("#s6-wheel", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 1.0, ease: "power4.out" }, 0.5);
    tl.fromTo("#s6-steer", { opacity: 0 }, { opacity: 1, duration: 0.7 }, 1.6);
    tl.fromTo("#s6-pedals", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.9, ease: "power4.out" }, 1.0);
    tl.fromTo("#s6-cb,#s6-cg", { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.6, stagger: 0.2, ease: "power4.out" }, 1.8);
    const order = [["#s6-L0", "#s6-ringLED", "s6-ln-L0"], ["#s6-L1", "#s6-ringPADL", "s6-ln-L1"],
      ["#s6-L2", "#s6-ringD", "s6-ln-L2"], ["#s6-L3", "#s6-ringCAM", "s6-ln-L3"],
      ["#s6-R0", "#s6-ringPADR", "s6-ln-R0"]];
    order.forEach((o, i) => {
      const t = 2.2 + i * 0.55;
      tl.fromTo(o[0], { opacity: 0, x: i < 4 ? -30 : 30 }, { opacity: 1, x: 0, duration: 0.8, ease: "power4.out" }, t);
      tl.fromTo(o[1], { opacity: 0 }, { opacity: 1, duration: 0.5 }, t);
      tl.fromTo("#" + o[2] + ",#" + o[2] + "b", { opacity: 0 }, { opacity: 1, duration: 0.5 }, t);
    });
    [["#s6-K0", "#s6-ringY"], ["#s6-K1", "#s6-ringX"], ["#s6-K2", "#s6-ringB"], ["#s6-K3", "#s6-ringA"]].forEach((o, i) => {
      const t = 5.2 + i * 0.55;
      tl.fromTo(o[0], { opacity: 0, x: 30 }, { opacity: 1, x: 0, duration: 0.8, ease: "power4.out" }, t);
      tl.fromTo(o[1], { opacity: 0 }, { opacity: 1, duration: 0.5 }, t);
    });
    tl.fromTo("#s6-foot", { opacity: 0 }, { opacity: 1, duration: 0.7 }, 7.6);
"""
    return scene_html("s6", body, js)


# ================================================================ wipes (the seams)
def wipes():
    items, js = [], []
    for k, seam in enumerate(SEAMS):
        items.append(f'''    <div id="wp{k}" class="clip wipe" data-start="{seam - 0.55:.2f}" data-duration="1.1" data-track-index="{k}">
      <div class="wk"></div><div class="wr"></div><div class="ww"></div></div>''')
        js.append(f'tl.fromTo("#wp{k}", {{ x: -3200 }}, {{ x: 2000, duration: 0.9, ease: "power3.inOut" }}, {seam - 0.45:.2f});')
    css = """
    #root { background: transparent; }
    .wipe { position: absolute; left: 0; top: 0; width: 3100px; height: 1080px; }
    .wk, .wr, .ww { position: absolute; top: 0; height: 1080px; }
    .wk { left: 0; width: 2900px; background: #15151e; clip-path: polygon(220px 0, 100% 0, calc(100% - 220px) 100%, 0 100%); }
    .wr { left: 200px; width: 2900px; background: #e10600; clip-path: polygon(220px 0, 100% 0, calc(100% - 220px) 100%, 0 100%); }
    .ww { left: 3040px; width: 60px; background: #ffffff; clip-path: polygon(30px 0, 100% 0, calc(100% - 30px) 100%, 0 100%); }
    """
    return scene_html("wipes", NL.join(items), NL.join("    " + x for x in js), css)


# ================================================================ index
def index():
    hosts = []
    for k, (cid, (a, b)) in enumerate(T.items()):
        hold = " data-hold" if cid == "s6" else ""
        hosts.append(f'''    <div id="{cid}" data-composition-id="{cid}" data-composition-src="compositions/{cid}.html"
      data-start="{a}" data-duration="{b - a}" data-track-index="{k + 1}" data-width="1920" data-height="1080" data-track-kind="graphics"{hold}></div>''')
    hosts.append(f'''    <div id="wipes" data-composition-id="wipes" data-composition-src="compositions/wipes.html"
      data-start="0" data-duration="{TOTAL}" data-track-index="9" data-width="1920" data-height="1080" data-track-kind="graphics" style="z-index:50"></div>''')
    return f"""<!doctype html>
<html lang="ko"
  data-composition-variables='[{{"id":"color-ink","type":"color","label":"Ink","default":"#15151e"}},{{"id":"color-red","type":"color","label":"Red","default":"#e10600"}},{{"id":"color-focus","type":"color","label":"Focus yellow","default":"#f0d430"}}]'>
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: 1920px; height: 1080px; overflow: hidden; background: #15151e; }}
      #root {{ position: relative; width: 1920px; height: 1080px; overflow: hidden; background: #15151e; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
{NL.join(hosts)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def main():
    cut_footage("--cuts" in sys.argv)
    (HERE / "compositions").mkdir(exist_ok=True)
    for name, fn in (("s1", s1), ("s2", s2), ("s3", s3), ("s4", s4), ("s5", s5), ("s6", s6), ("wipes", wipes)):
        (HERE / "compositions" / f"{name}.html").write_text(fn(), encoding="utf-8")
    (HERE / "index.html").write_text(index(), encoding="utf-8")
    print("built", TOTAL, "s", {k: v for k, v in T.items()})


if __name__ == "__main__":
    main()
