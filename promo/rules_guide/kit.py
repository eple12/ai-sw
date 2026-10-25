"""Building blocks for the rules video.

A card is the header, rows of "chip + short text" on the left, and a stage on the right
(in-game footage or an in-game screenshot). Every picture is the game itself; nothing here draws
illustrations. Line breaks are decided here, in Python, never by the browser:

    "a | b | c"     phrases: a line never breaks inside one, only between them
    "\\n"            a hard break (a new sentence on a new line)
    [[x]] {{x}} ((x))   yellow / red / green words (inside one phrase)

A phrase that is wider than the line falls back to breaking between words (어절), never inside one.
"""
from __future__ import annotations

import re

NL = chr(10)

INK, PANEL, PANEL_HI = "#15151e", "#1f1f2b", "#2a2a38"
RED, YELLOW, GREEN, GREY, DIM = "#e10600", "#f0d430", "#3cd25a", "#969aa2", "#60626c"
TONE = {"y": (YELLOW, INK), "r": (RED, "#fff"), "g": (GREEN, INK), "d": (PANEL_HI, GREY)}
SPAN = {"y": YELLOW, "r": "#ff5a4f", "g": GREEN}

STAGE_X, STAGE_Y, STAGE_W = 1032, 150, 792
DUR = "@@DUR@@"                 # the card's length, filled in when it is built

CSS = """
@font-face{font-family:"Bahn";src:local("Bahnschrift"),local("Bahnschrift Regular");font-weight:100 900}
@font-face{font-family:"NotoKR";src:url("assets/fonts/NotoSansKR-VF.ttf") format("truetype");font-weight:100 900}
#root{position:absolute;inset:0;overflow:hidden;background:transparent;color:#fff;font-family:"NotoKR",sans-serif}
#root *{margin:0;padding:0;box-sizing:border-box}
.lat{font-family:"Bahn","NotoKR",sans-serif;font-weight:700}
.abs{position:absolute}
.w{display:inline-block;white-space:pre}
.h{font-weight:900;letter-spacing:-0.02em;line-height:1.14;white-space:nowrap}
.sub{font-weight:700;color:#969aa2;white-space:nowrap}
.chip{position:absolute;font-weight:900;text-align:center;white-space:nowrap;
  clip-path:polygon(14px 0,100% 0,calc(100% - 14px) 100%,0 100%)}
.txt{position:absolute;font-weight:700;letter-spacing:-0.02em;white-space:nowrap}
.frame{position:absolute;overflow:hidden;background:#000}
.band{position:absolute;height:8px;background:#e10600}
"""


# ------------------------------------------------------------------ text fitting
def tw(s: str, size: float) -> float:
    """Width in px of *s* in Noto Sans KR 700-900 (an estimate, tuned on the renders)."""
    w = 0.0
    for ch in s:
        if ch == " ":
            w += 0.28
        elif ch == "·":
            w += 0.42
        elif ch in ",.:;!'":
            w += 0.30
        elif ch in "()[]/-+~":
            w += 0.40
        elif ch.isdigit():
            w += 0.60
        elif ch.isascii() and ch.isupper():
            w += 0.70
        elif ch.isascii():
            w += 0.56
        else:
            w += 0.98
    return (w - 0.02 * len(s)) * size


def parse(text: str):
    """'a [[b c]]d' -> words, each a list of (fragment, style)."""
    style, words = None, [[]]
    for t in re.split(r"(\[\[|\]\]|\{\{|\}\}|\(\(|\)\))", text):
        if t == "[[":
            style = "y"
        elif t == "{{":
            style = "r"
        elif t == "((":
            style = "g"
        elif t in ("]]", "}}", "))"):
            style = None
        else:
            for i, part in enumerate(t.split(" ")):
                if i:
                    words.append([])
                if part:
                    words[-1].append((part, style))
    return [w for w in words if w]


def _wlen(word, size):
    return sum(tw(f, size) for f, _ in word)


def _plen(words, size):
    return sum(_wlen(w, size) for w in words) + 0.28 * size * max(len(words) - 1, 0)


def wrap(text: str, width: float, size: float):
    """Lines (each a list of words) for marked-up *text*, broken only between phrases ('|') and,
    for a phrase wider than the line, between words."""
    width = width * 0.97
    gap = 0.28 * size
    out = []
    for hard in text.split(NL):
        phrases = [parse(p.strip()) for p in hard.split("|") if p.strip()]
        lines, cur, cw = [], [], 0.0                   # a line = list of phrases
        for ph in phrases:
            pw = _plen(ph, size)
            if cur and cw + gap + pw <= width:
                cur.append(ph)
                cw += gap + pw
                continue
            if cur:
                lines.append(cur)
                cur, cw = [], 0.0
            if pw <= width:
                cur, cw = [ph], pw
            else:                                       # one phrase too wide: between words
                row, rw = [], 0.0
                for w in ph:
                    wl = _wlen(w, size)
                    if row and rw + gap + wl > width:
                        lines.append([row])
                        row, rw = [w], wl
                    else:
                        row.append(w)
                        rw += (gap if len(row) > 1 else 0) + wl
                cur, cw = [row], rw
        if cur:
            lines.append(cur)
        # do not leave a lone short phrase hanging on the last line
        while (len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 2
               and _plen(lines[-1][0], size) < 0.42 * width
               and sum(_plen(p, size) for p in lines[-2][-1:]) + gap + _plen(lines[-1][0], size) <= width):
            lines[-1].insert(0, lines[-2].pop())
        out.extend(lines)
    return [[w for ph in line for w in ph] for line in out]


def lines_text(lines) -> str:
    return NL.join(" ".join("".join(f for f, _ in w) for w in line) for line in lines)


def html_lines(lines) -> str:
    out = []
    for line in lines:
        words = []
        for word in line:
            words.append("".join(f'<span style="color:{SPAN[st]}">{f}</span>' if st else f for f, st in word))
        out.append(" ".join(words))
    return "<br>".join(out)


def n_words(text: str) -> int:
    return sum(len(parse(p)) for p in re.split(r"[|\n]", text) if p.strip())


def mk(text: str) -> str:
    """Inline markup -> spans, for raw html strings."""
    text = re.sub(r"\[\[(.+?)\]\]", lambda m: f'<span style="color:{SPAN["y"]}">{m.group(1)}</span>', text)
    text = re.sub(r"\{\{(.+?)\}\}", lambda m: f'<span style="color:{SPAN["r"]}">{m.group(1)}</span>', text)
    text = re.sub(r"\(\((.+?)\)\)", lambda m: f'<span style="color:{SPAN["g"]}">{m.group(1)}</span>', text)
    return text


def words_html(text: str, cls: str = "w") -> str:
    parts = text.split(" ")
    out = []
    for i, w in enumerate(parts):
        out.append(f'<span class="{cls}">{w}</span>')
        if i < len(parts) - 1:
            out.append('<span class="w"> </span>')
    return "".join(out)


def tpl(text: str, **kw) -> str:
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
    const WORDS = (sel, t, dur = 0.25, gap = 0.18) =>
      tl.fromTo(sel, { opacity: 0, filter: "blur(6px)" },
        { opacity: 1, filter: "blur(0px)", duration: dur, stagger: gap, ease: "power2.out" }, t);
    const IN = (sel, t, from = {}, dur = 0.8) =>
      tl.fromTo(sel, { opacity: 0, filter: "blur(6px)", ...from },
        { opacity: 1, filter: "blur(0px)", x: 0, y: 0, duration: dur, ease: "power4.out" }, t);
$js$
    window.__timelines["$cid$"] = tl;
  </script>
</template>
""", css=CSS + extra_css, cid=cid, body=body, js=js)


def clip_attrs(start, dur, idx=0):
    return f'data-start="{start}" data-duration="{dur}" data-track-index="{idx}"'


# ------------------------------------------------------------------ the settings screen (real stills)
SET_X0, SET_X1 = 77, 1835          # the rows' left edge (with the focus bar) and right edge
SET_TOP, SET_PITCH, SET_H = 308, 73.4, 67
SET_VISIBLE = 9


def set_row_rect(sel: int):
    """(x, y, w, h) of row *sel* in its own 1920 x 1080 still (the list scrolls once past nine rows)."""
    first = max(0, sel - (SET_VISIBLE - 1))
    j = sel - first
    top = SET_TOP + SET_PITCH * j
    return SET_X0, top - 3, SET_X1 - SET_X0, SET_H + 6


# ------------------------------------------------------------------ the card
class Card:
    """One composition: header, rows, stage. Everything sits inside a wrapper that fades with the
    card, so a card change inside a chapter is a swap, not a cut."""

    READ_BASE, READ_WORD = 1.1, 0.27         # seconds a row is read before the next one comes
    HOLD_BASE = 2.2                          # the last row stays at least this long, plus its reading time

    def __init__(self, cid: str, chapter: str = "", topic: str = "", dur: float | None = None,
                 header: bool = True, fade_in: bool = True, min_dur: float = 9.0):
        self.cid = cid
        self.dur = dur
        self.min_dur = min_dur
        self.fade_in = fade_in
        self.chapter, self.topic = chapter, topic
        self.html: list[str] = []
        self.js: list[str] = []
        self.css = ""
        self.t_end = 0.0
        self.report: list[str] = []
        if header:
            self._header()

    def u(self, name: str) -> str:
        return f"{self.cid}-{name}"

    def add(self, html: str = "", js: str = ""):
        if html:
            self.html.append(html)
        if js:
            self.js.append(js)

    def _header(self):
        cls = self.u("hh")
        txt = f'<span class="w {cls}" style="color:{YELLOW}">{self.chapter}</span>'
        if self.topic:
            txt += '<span class="w"> </span>' + "".join(
                f'<span class="w {cls}">{w}</span><span class="w"> </span>' for w in self.topic.split(" "))
        self.add(f'<div id="{self.u("h")}" class="abs h" style="left:96px;top:40px;font-size:68px">{txt}</div>',
                 f'WORDS("#{self.u("h")} .{cls}", 0.5, 0.25, 0.16);')

    # -- timing
    def row_times(self, texts, t0=1.6):
        t, out = t0, []
        for tx in texts:
            out.append(round(t, 2))
            t += self.READ_BASE + self.READ_WORD * n_words(tx)
        end = out[-1] + self.HOLD_BASE + self.READ_WORD * n_words(texts[-1]) + 0.4 if texts else t0
        self.t_end = max(self.t_end, end)
        return out

    # -- rows: chip + text
    def rows(self, items, x=96, w=880, y0=176, size=42, maxh=830, row_gap=28, t0=1.6, times=None):
        n = len(items)
        cw = max(max(190, int(tw(it["key"], it.get("cs", 34)) + 70)) for it in items)
        tx, tww = x + cw + 28, w - cw - 28
        while True:
            lay = [wrap(it["text"], tww, size) for it in items]
            lh = size * 1.34
            hs = [max(62, len(l) * lh) for l in lay]
            if sum(hs) + row_gap * (n - 1) <= maxh or size <= 34:
                break
            size -= 2
        ts = times or self.row_times([it["text"] for it in items], t0)
        y = y0
        for k, (it, lines, h) in enumerate(zip(items, lay, hs)):
            bg, fg = TONE[it.get("tone", "y")]
            cfs = it.get("cs", 34)
            rid = self.u(f"r{k}")
            self.add(
                f'<div id="{rid}" class="abs" style="left:0;top:{y:.0f}px;width:1920px;height:{h:.0f}px;opacity:0">'
                f'<div class="chip lat" style="left:{x}px;top:0;width:{cw}px;height:60px;line-height:60px;'
                f'font-size:{cfs}px;background:{bg};color:{fg}">{it["key"]}</div>'
                f'<div class="txt" style="left:{tx}px;top:0;font-size:{size}px;line-height:{lh:.0f}px">'
                f'{html_lines(lines)}</div></div>',
                f'IN("#{rid}", {ts[k]:.2f}, {{ x: 40 }}, 0.8);')
            self.report.append(f"  [{it['key']}] " + lines_text(lines).replace(NL, "  /  "))
            y += h + row_gap
        return y, ts

    # -- stage: in-game footage
    def stage_video(self, src: str, t: float = 0.7, y: int = STAGE_Y, h: int = 446, start=0,
                    vid_idx=0, x: int = STAGE_X, w: int = STAGE_W):
        fid = self.u(f"v{vid_idx}")
        self.add(
            f'<div class="band" style="left:{x}px;top:{y - 8}px;width:{w}px"></div>'
            f'<div id="{fid}f" class="frame" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;opacity:0">'
            f'<video id="{fid}" class="clip abs" src="assets/footage/{src}.mp4" muted playsinline '
            f'style="left:0;top:0;width:{w}px;height:{h}px;object-fit:cover" '
            f'{clip_attrs(start, DUR, vid_idx)}></video></div>',
            f'tl.fromTo("#{fid}f", {{opacity:0}}, {{opacity:1, duration:0.6, ease:"power2.out"}}, {t});')

    def crop_video(self, src: str, rect, y: int, t: float = 1.0, start=0, vid_idx=1,
                   x: int = STAGE_X, w: int = STAGE_W, windows=None):
        """A magnified piece of the footage (rect = x, y, w, h in the 1920 x 1080 picture), *w* px wide.
        With *windows* ([[from, to], ...] in seconds of the clip) it shows only while something is
        there to see, e.g. a RACE CONTROL message, and is hidden the rest of the time."""
        rx, ry, rw, rh = rect
        kk = w / rw
        fh = int(rh * kk)
        fid = self.u(f"c{vid_idx}")
        if windows is None:
            js = f'tl.fromTo("#{fid}f", {{opacity:0}}, {{opacity:1, duration:0.6, ease:"power2.out"}}, {t});'
        else:
            js = ""
            for i, (a, b) in enumerate(windows):
                ir = "" if i == 0 else ", immediateRender:false"
                js += f'tl.fromTo("#{fid}f", {{opacity:0}}, {{opacity:1, duration:0.3, ease:"power2.out"{ir}}}, {max(a - 0.1, 0):.2f});'
                js += f'tl.to("#{fid}f", {{opacity:0, duration:0.3, ease:"power2.in", immediateRender:false}}, {b:.2f});'
        self.add(
            f'<div id="{fid}f" class="frame" style="left:{x}px;top:{y}px;width:{w}px;height:{fh}px;'
            f'opacity:0;border:2px solid {PANEL_HI}">'
            f'<video id="{fid}" class="clip abs" src="assets/footage/{src}.mp4" muted playsinline '
            f'style="left:{-rx * kk:.1f}px;top:{-ry * kk:.1f}px;width:{1920 * kk:.1f}px;height:{1080 * kk:.1f}px;'
            f'max-width:none" {clip_attrs(start, DUR, vid_idx)}></video></div>', js)
        return fh

    def still(self, src: str, rect, x: int, y: int, w: int, t: float, name: str = "s", native: int = 1920):
        """A magnified piece of an in-game screenshot (rect = x, y, w, h in the still's own pixels,
        *native* px wide)."""
        rx, ry, rw, rh = rect
        k = w / rw
        fh = int(rh * k)
        fid = self.u(name)
        self.add(
            f'<div id="{fid}" class="frame" style="left:{x}px;top:{y}px;width:{w}px;height:{fh}px;'
            f'opacity:0;border:2px solid {PANEL_HI}">'
            f'<img src="assets/img/{src}" style="position:absolute;left:{-rx * k:.1f}px;top:{-ry * k:.1f}px;'
            f'width:{native * k:.1f}px;max-width:none"/></div>',
            f'IN("#{fid}", {t:.2f}, {{ y: 24 }}, 0.9);')
        return fh

    # -- settings: the real row, then what it does
    def setting(self, k: int, y: int, sel: int, text: str, t: float, tag: str = "", tag_tone="g",
                x=96, w=1728, size=42, tag_w=300, text_w=None, tag_below=False):
        """The game's own row *sel* (cropped from a real screenshot, *w* wide), the text under it, and a tag.
        The tag sits at the right of the text, or under it (*tag_below*). *text_w* narrows the text column
        (to leave room for a video). Returns the y below the block."""
        rx, ry, rw, rh = set_row_rect(sel)
        s = w / rw
        fh = int(rh * s)
        bid = self.u(f"b{k}")
        if text_w is None:
            text_w = w - (tag_w + 30 if tag and not tag_below else 0)
        lines = wrap(text, text_w, size)
        lh = size * 1.34
        th = len(lines) * lh
        chip = ""
        extra = 0
        if tag:
            bg, fg = TONE[tag_tone]
            tfs = 30 if tw(tag, 30) + 50 <= tag_w else 26
            if tag_below:
                chip = (f'<div class="chip lat" style="left:6px;top:{fh + 14 + th + 18:.0f}px;width:{tag_w}px;height:58px;'
                        f'line-height:58px;font-size:{tfs}px;background:{bg};color:{fg}">{tag}</div>')
                extra = 18 + 58
            else:
                chip = (f'<div class="chip lat" style="left:{w - tag_w}px;top:{fh + 18}px;width:{tag_w}px;height:58px;'
                        f'line-height:58px;font-size:{tfs}px;background:{bg};color:{fg}">{tag}</div>')
        total = fh + 14 + th + extra
        self.add(
            f'<div id="{bid}" class="abs" style="left:{x}px;top:{y}px;width:{w}px;height:{total:.0f}px;opacity:0">'
            f'<div class="frame" style="left:0;top:0;width:{w}px;height:{fh}px">'
            f'<img src="assets/settings/row_{sel:02d}.png" style="position:absolute;left:{-rx * s:.1f}px;top:{-ry * s:.1f}px;'
            f'width:{1920 * s:.1f}px;max-width:none"/></div>'
            f'<div class="txt" style="left:6px;top:{fh + 14}px;font-size:{size}px;line-height:{lh:.0f}px">{html_lines(lines)}</div>'
            f'{chip}</div>',
            f'IN("#{bid}", {t:.2f}, {{ x: 40 }}, 0.8);')
        self.report.append(f"  <{sel:02d}> " + lines_text(lines).replace(NL, "  /  "))
        return y + total

    # -- finish
    def build(self) -> str:
        if self.dur is None:
            self.dur = max(self.min_dur, round(self.t_end * 2) / 2)
        wrap_id = self.u("all")
        d = self.dur
        body = (f'<div id="{wrap_id}" class="abs" style="left:0;top:0;width:1920px;height:1080px">'
                + NL.join(self.html) + "</div>").replace(DUR, f"{d}")
        js = NL.join(self.js).replace(DUR, f"{d}") + tpl("""
$fade$
    tl.to("#$w$", { opacity: 0, filter: "blur(8px)", duration: 0.4, ease: "power2.in", immediateRender: false }, $out$);
""", w=wrap_id, out=f"{d - 0.45:.2f}",
                 fade=('tl.fromTo("#%s", { opacity: 0 }, { opacity: 1, duration: 0.4, ease: "power2.out" }, 0);' % wrap_id)
                 if self.fade_in else "")
        return scene_html(self.cid, body, js, self.css)
