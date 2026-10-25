#!/usr/bin/env python
"""Build the rules video: sessions, stewards, difficulty and settings. Everything on screen is the
game itself (footage and screenshots shot by tools/shoot.py) plus text.

    python build.py            # footage cuts (if missing), settings stills, html
    python build.py --cuts     # redo the footage cuts too

Rules on screen come from the code: rules.py / racecontrol.py (track limits, contact, yellow),
field.py (start, DRS, slipstream, finish), app.py (qualifying laps), teams.py (levels), settings.py.
The generated html is overwritten by each build: change the video here, not in Studio.

Copy style: -습니다 sentences, or noun phrases ("대기", "5초"). No keyboard keys (the controls have
their own video). Phrases are joined with " | " so a line breaks only between them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kit import (Card, scene_html, clip_attrs, words_html, mk, NL, STAGE_X, STAGE_Y, STAGE_W, INK, PANEL_HI,
                 RED, YELLOW, GREEN, GREY, TONE, tw, wrap, html_lines, lines_text)

HERE = Path(__file__).resolve().parent
FOOT = HERE.parent / "footage"
FPS = 30

# ---------------------------------------------------------------- footage windows
#: name -> (source, start, length) or [(source, start, end), ...] joined; cut to assets/footage/<name>.mp4.
#: Clips are cut longer than the card that uses them; the card's own length ends the clip.
SEG = {
    "opening":  ("gp_start", 0.5, 6.5),
    "q_flow":   ("q_flow", 0.0, 14.0),
    "tl_line":  ("tl_wheels", 0.5, 14.5),
    "q_fin":    ("q_finish", 0.0, 12.0),
    "grid":     ("grid_menu", 0.0, 15.5),
    "lights":   ("lights_cam", 0.0, 10.5),
    "start":    ("gp_start", 0.5, 13.5),
    "jump":     ("gp_jump", 4.0, 12.0),
    #: one real RACE CONTROL message of each kind: track limits, contact, yellow flag, jump start
    "msgs":     [("gp_limits", 13.5, 17.5), ("collide", 14.2, 18.2), ("gp_yellow", 22.5, 26.5),
                 ("gp_jump", 7.5, 11.5)],
    #: the track-limits ladder: warnings 1-4, the black and white flag, the first time penalty
    "lim_msgs": [("gp_limits", 12.8, 16.0), ("gp_limits", 17.8, 20.8), ("gp_limits", 22.8, 25.5),
                 ("gp_limits", 29.8, 31.8), ("gp_limits", 32.8, 34.5), ("gp_limits", 35.0, 37.0)],
    "contact":  [("collide", 13.2, 19.5), ("collide", 26.5, 33.0)],
    "yellow":   ("gp_yellow", 17.0, 14.7),
    "slip":     ("slip_gp", 11.0, 14.0),
    "wall":     ("wall", 0.5, 14.5),
    "levels":   ("diff_menu", 0.0, 26.0),
    "settings": ("settings_scroll", 0.0, 12.0),
    "rline":    ("rline", 6.0, 14.0),
    "shift":    ("shift", 0.2, 12.0),
}


def cut_footage(force: bool):
    out = HERE / "assets" / "footage"
    out.mkdir(parents=True, exist_ok=True)
    enc = ["-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-g", "30",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    for name, spec in SEG.items():
        dst = out / f"{name}.mp4"
        if dst.exists() and not force:
            continue
        print("cut", name)
        if isinstance(spec, list):
            ins, parts = [], []
            for i, (src, a, z) in enumerate(spec):
                ins += ["-i", str(FOOT / f"{src}.mp4")]
                parts.append(f"[{i}:v]trim={a}:{z},setpts=PTS-STARTPTS[v{i}];")
            cat = "".join(f"[v{i}]" for i in range(len(spec))) + f"concat=n={len(spec)}:v=1:a=0[o]"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + ins
                           + ["-filter_complex", "".join(parts) + cat, "-map", "[o]"] + enc + [str(dst)], check=True)
        else:
            src, t0, dur = spec
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0}",
                            "-i", str(FOOT / f"{src}.mp4"), "-t", f"{dur}"] + enc + [str(dst)], check=True)


def copy_stills():
    """The settings screen, one real still per row (shot by tools/shoot.py: settings_scroll)."""
    out = HERE / "assets" / "settings"
    out.mkdir(parents=True, exist_ok=True)
    for k in range(18):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{2.0 + 3 * k}",
                        "-i", str(FOOT / "settings_scroll.mp4"), "-frames:v", "1", str(out / f"row_{k:02d}.png")],
                       check=True)


def check_glitches():
    """A menu clip must not hold a half-drawn frame (see glitch.py): warn loudly if one does."""
    from glitch import find, read
    for name in ("grid", "levels", "settings"):
        bad = find(read(str(HERE / "assets" / "footage" / f"{name}.mp4")))
        if bad:
            print(f"WARNING: {name}.mp4 has {len(bad)} glitch frames {bad[:10]}: reshoot it")


def _windows():
    f = HERE / "assets" / "footage" / "msg_windows.json"
    return json.loads(f.read_text()) if f.exists() else {}


def mw(clip):
    """When the RACE CONTROL message bar is up in a cut clip (msgscan.py)."""
    return _windows().get(clip, [])


#: Where the stewards' message bar sits in the 1920 x 1080 picture.
MSG = (700, 20, 520, 84)
MSG_WIDE = (520, 20, 880, 84)
DRS_HUD = (560, 700, 480, 200)          # in the 1600 x 900 DRS screenshots


# ================================================================ cards
def c_open(cid):
    c = Card(cid, dur=6.5, header=False, fade_in=False)
    c.add(f'<div id="{cid}-vw" class="abs" style="left:0;top:0;width:1920px;height:1080px">'
          f'<video id="{cid}-v" class="clip abs" src="assets/footage/opening.mp4" muted playsinline '
          f'style="left:0;top:0;width:1920px;height:1080px;object-fit:cover" {clip_attrs(0, 6.5)}></video></div>'
          f'<div id="{cid}-pnr" class="abs" style="left:0;top:0;width:1040px;height:1080px;background:{RED};'
          f'clip-path:polygon(0 0,100% 0,calc(100% - 100px) 100%,0 100%)"></div>'
          f'<div id="{cid}-pn" class="abs" style="left:0;top:0;width:1020px;height:1080px;background:{INK};'
          f'clip-path:polygon(0 0,100% 0,calc(100% - 100px) 100%,0 100%)"></div>'
          f'<img id="{cid}-logo" class="abs" src="assets/img/fai_logo.png" style="left:96px;top:250px;width:620px;opacity:0"/>'
          f'<div id="{cid}-t" class="abs h" style="left:88px;top:480px;font-size:170px;line-height:1.1">'
          f'{words_html("규칙", f"w {cid}m")}<span class="w {cid}m"> · </span>{words_html("설정", f"w {cid}m")}</div>',
          f"""
    tl.fromTo("#{cid}-vw", {{ x: 0 }}, {{ x: 560, duration: 0.9, ease: "power4.out" }}, 1.0);
    tl.fromTo("#{cid}-pnr", {{ x: -1300 }}, {{ x: 0, duration: 0.9, ease: "power4.out" }}, 1.0);
    tl.fromTo("#{cid}-pn", {{ x: -1300 }}, {{ x: 0, duration: 0.9, ease: "power4.out" }}, 1.1);
    IN("#{cid}-logo", 1.9, {{ x: -60 }}, 0.9);
    WORDS("#{cid}-t .{cid}m", 2.8, 0.3, 0.3);
""")
    return c


def c_sessions(cid):
    c = Card(cid, "세션", "두 가지", dur=12.0)
    cols = [(96, "QUALIFYING", "퀄리파잉", "혼자 달려 기록을 냅니다", "아웃랩 1바퀴 + 기록 랩", "q_flow"),
            (1004, "GRAND PRIX", "그랑프리", "AI 19대와 함께 20대가 출발합니다", "접촉 · DRS · 옐로 플래그 · 페널티", "start")]
    for k, (x, en, ko, a, b, vid) in enumerate(cols):
        t0 = 1.2 + k * 3.0
        c.add(f'<div id="{cid}-k{k}" class="abs" style="left:{x}px;top:150px;width:820px;height:850px;opacity:0">'
              f'<div class="band" style="left:0;top:0;width:820px"></div>'
              f'<div class="frame" style="left:0;top:8px;width:820px;height:380px">'
              f'<video id="{cid}-v{k}" class="clip abs" src="assets/footage/{vid}.mp4" muted playsinline '
              f'style="left:0;top:0;width:820px;height:461px;object-fit:cover" {clip_attrs(0, 12.0, k)}></video></div>'
              f'<div class="abs lat" style="left:0;top:420px;font-size:48px;color:{YELLOW};letter-spacing:0.06em">{en}</div>'
              f'<div class="abs h" style="left:0;top:476px;font-size:110px">{ko}</div>'
              f'<div class="txt" style="left:0;top:640px;font-size:44px;line-height:64px">{a}<br>{b}</div></div>',
              f'IN("#{cid}-k{k}", {t0}, {{ y: 30 }}, 0.9);')
    return c


# ---- qualifying --------------------------------------------------------------------------
def c_q_flow(cid):
    c = Card(cid, "퀄리파잉", "진행")
    c.rows([dict(key="아웃랩", text="첫 바퀴는 | 기록되지 않습니다", tone="d"),
            dict(key="기록 랩", text="결승선을 지나면 | 시작됩니다", tone="y"),
            dict(key="종료", text="정해진 랩을 마치면 | 체커 플래그와 결과가 나옵니다", tone="g")])
    c.stage_video("q_flow")
    return c


def c_q_invalid(cid):
    c = Card(cid, "퀄리파잉", "랩 삭제")
    c.rows([dict(key="삭제", text="네 바퀴가 모두 | 흰 선 밖으로 나가면 | 그 랩은 삭제됩니다", tone="r"),
            dict(key="정상", text="한 바퀴라도 | 선 안에 있으면 | 괜찮습니다", tone="g"),
            dict(key="복귀", text="코스 복귀를 쓰면 | 그 랩도 삭제됩니다", tone="r"),
            dict(key="기록", text="삭제된 랩은 | 베스트와 섹터 기록에 | 반영되지 않습니다", tone="d"),
            dict(key="초심자", text="TRACK-LIMIT WALL을 켜면 | 랩 삭제를 피할 수 있습니다", tone="g")])
    c.stage_video("tl_line")
    c.crop_video("tl_line", MSG, y=620, windows=mw("tl_line"))
    return c


def c_q_result(cid):
    c = Card(cid, "퀄리파잉", "기록")
    c.rows([dict(key="베스트", text="가장 빠른 유효 랩이 | 내 기록입니다", tone="y"),
            dict(key="타워", text="같은 난이도 | AI 19명의 예선 기록이 | 함께 표시됩니다", tone="y"),
            dict(key="고스트", text="폴 드라이버의 베스트 랩이 | 고스트로 함께 달립니다", tone="d"),
            dict(key="그리드", text="게임을 켜 둔 동안 | 같은 서킷·난이도 그랑프리의 | 출발 순위가 됩니다", tone="g")])
    c.stage_video("q_fin")
    return c


# ---- grand prix --------------------------------------------------------------------------
def c_gp_grid(cid):
    c = Card(cid, "그랑프리", "출발 순위")
    c.rows([dict(key="20대", text="10팀 × 2대, | 내 차는 한 자리입니다", tone="d"),
            dict(key="선택", text="서킷 선택 화면에서 | 출발 순위를 고릅니다", tone="y"),
            dict(key="QUALI", text="내 퀄리파잉 기록 순위, | 또는 1~20번 직접 지정", tone="y"),
            dict(key="기록 없음", text="퀄리파잉 기록이 없으면 | 10번에서 출발합니다", tone="g")])
    c.stage_video("grid")
    return c


def c_gp_start(cid):
    c = Card(cid, "그랑프리", "출발")
    c.rows([dict(key="신호", text="빨간 불 5개가 | 차례로 켜집니다", tone="r"),
            dict(key="출발", text="불이 모두 꺼지면 | 출발합니다", tone="g"),
            dict(key="대기", text="그전에는 | 차량 모두 움직이지 않고 대기", tone="d")])
    c.stage_video("lights")
    return c


def c_gp_jump(cid):
    c = Card(cid, "그랑프리", "점프 스타트")
    c.rows([dict(key="5초", text="불이 다 켜진 뒤 | 스로틀을 절반 넘게 밟으면 | 페널티 5초", tone="r"),
            dict(key="AI", text="AI도 가끔 | 성급하게 출발합니다", tone="d"),
            dict(key="설정", text="JUMP START PENALTY를 끄면 | 페널티가 없습니다", tone="y")])
    c.stage_video("jump")
    c.crop_video("jump", MSG, y=620, windows=mw("jump"))
    return c


def c_gp_finish(cid):
    c = Card(cid, "그랑프리", "결승")
    c.rows([dict(key="체커", text="선두가 정해진 랩을 마치면 | 체커 플래그", tone="y"),
            dict(key="마감", text="나머지는 다음에 | 결승선을 지나면 종료.\n제한 시간 안에 못 들어오면 | 그 순위로 확정", tone="y"),
            dict(key="페널티", text="시간 페널티는 | 결과 기록에 더해집니다", tone="r")])
    c.add(f'<div class="band" style="left:{STAGE_X}px;top:142px;width:{STAGE_W}px"></div>'
          f'<div id="{cid}-img" class="frame" style="left:{STAGE_X}px;top:150px;width:{STAGE_W}px;height:446px;opacity:0">'
          f'<img src="assets/img/gp_result.png" style="position:absolute;left:0;top:0;width:{STAGE_W}px;height:446px;object-fit:cover"/></div>',
          f'tl.fromTo("#{cid}-img", {{opacity:0}}, {{opacity:1, duration:0.6}}, 0.8);')
    return c


# ---- stewards: a short tour, not the rulebook ---------------------------------------------
def c_rc_overview(cid):
    c = Card(cid, "스튜어드", "판정")
    c.rows([dict(key="트랙 리밋", text="코스 밖으로 나간 경우", tone="y"),
            dict(key="충돌", text="접촉의 책임이 | 누구에게 있는지", tone="y"),
            dict(key="옐로 플래그", text="속도 제한과 추월 금지", tone="y"),
            dict(key="점프 스타트", text="출발 신호 중 가속", tone="y"),
            dict(key="표시", text="판정은 | RACE CONTROL 메시지로 | 화면 위에 표시됩니다", tone="d"),
            dict(key="끄기", text="설정의 PENALTY 항목으로 | 종류별로 끌 수 있습니다", tone="g")], size=40)
    c.stage_video("msgs")
    c.crop_video("msgs", MSG_WIDE, y=620, windows=mw("msgs"))
    return c


def c_tl(cid):
    c = Card(cid, "스튜어드", "트랙 리밋")
    c.rows([dict(key="이탈", text="네 바퀴가 모두 | 흰 선 밖으로 나가면 | 트랙 이탈입니다", tone="r"),
            dict(key="이득", text="질러가서 시간을 벌면 | 시간 페널티입니다", tone="r"),
            dict(key="경고", text="넓게 나간 정도라면 | 경고가 쌓이고, | 5회째 흑백 깃발, 이후 페널티", tone="y"),
            dict(key="조치 없음", text="접촉으로 밀려났거나 | 이미 시간을 잃은 경우", tone="g"),
            dict(key="초심자", text="TRACK-LIMIT WALL을 켜면 | 이탈 자체가 막힙니다", tone="g")])
    c.stage_video("lim_msgs")
    c.crop_video("lim_msgs", MSG, y=620, windows=mw("lim_msgs"))
    return c


def c_collision(cid):
    c = Card(cid, "스튜어드", "충돌")
    c.rows([dict(key="과실", text="뒤에서 들이받은 쪽, | 옆으로 파고든 쪽에 | 책임이 있습니다", tone="r"),
            dict(key="페널티", text="충격이 크거나 | 상대가 스핀했다면 5초,\n일부러 들이받은 듯해도 5초", tone="r"),
            dict(key="경고", text="가벼운 접촉은 | 경고에 그칩니다", tone="y"),
            dict(key="조치 없음", text="피할 수 없던 접촉, | 첫 랩의 가벼운 접촉", tone="g")])
    c.stage_video("contact")
    c.crop_video("contact", MSG_WIDE, y=620, windows=mw("contact"))
    return c


def c_yellow(cid):
    c = Card(cid, "스튜어드", "옐로 플래그")
    c.rows([dict(key="발생", text="멈춘 차가 있으면 | 그 앞뒤 구간에 | 옐로 플래그가 나옵니다", tone="y"),
            dict(key="속도", text="구간 안에서는 | 시속 100km 이하", tone="r"),
            dict(key="추월", text="추월 금지. | 앞질렀다면 자리를 돌려줘야 합니다", tone="r"),
            dict(key="위반", text="어기면 경고나 | 5초 페널티", tone="d")])
    c.stage_video("yellow")
    c.crop_video("yellow", MSG_WIDE, y=620, windows=mw("yellow"))
    return c


# ---- overtaking ----------------------------------------------------------------------------
def c_drs(cid):
    c = Card(cid, "추월", "DRS")
    c.rows([dict(key="조건", text="직선에서 앞차와 | 1.8초 이내", tone="y"),
            dict(key="효과", text="공기저항이 줄어 | 최고 속도가 오릅니다", tone="g"),
            dict(key="첫 랩", text="첫 랩에는 쓸 수 없습니다", tone="d"),
            dict(key="열기", text="기본은 자동. | MANUAL DRS를 켜면 직접 열고, | 브레이크를 밟으면 닫힙니다", tone="y")])
    fh = c.still("drs_ready.png", DRS_HUD, STAGE_X, 190, STAGE_W, 1.2, native=1600, name="s0")
    c.still("drs_open.png", DRS_HUD, STAGE_X, 190 + fh + 70, STAGE_W, 6.0, native=1600, name="s1")
    c.add(f'<div id="{cid}-l0" class="txt" style="left:{STAGE_X}px;top:{190 + fh + 12}px;font-size:36px;color:{GREY};opacity:0">DRS 표시가 깜빡이면</div>'
          f'<div id="{cid}-l1" class="txt" style="left:{STAGE_X}px;top:{190 + 2 * fh + 70 + 12}px;font-size:36px;color:{GREEN};opacity:0">누르면 DRS OPEN</div>',
          f'IN("#{cid}-l0", 1.6, {{}}, 0.6); IN("#{cid}-l1", 6.4, {{}}, 0.6);')
    return c


def c_slip(cid):
    c = Card(cid, "추월", "슬립스트림")
    c.rows([dict(key="거리", text="앞차 뒤 40m 이내", tone="y"),
            dict(key="정렬", text="옆으로 1.6m 이내로 | 겹쳐 달리면", tone="y"),
            dict(key="효과", text="공기저항이 최대 30% 줄어 | 더 빨라집니다", tone="g")])
    c.stage_video("slip")
    return c


# ---- difficulty ------------------------------------------------------------------------------
def c_levels(cid):
    c = Card(cid, "난이도", "AI")
    c.rows([dict(key="1 Novice", text="일찍 브레이크하고 | 자주 실수합니다", tone="d", cs=30),
            dict(key="2 Rookie", text="안정적이고 | 따라잡을 만합니다", tone="d", cs=30),
            dict(key="3 Amateur", text="코너가 빠르지만 | 아직 실수합니다", tone="y", cs=30),
            dict(key="4 Club", text="제대로 된 레이스", tone="y", cs=30),
            dict(key="5 Pro", text="한계에 가깝고 | 거의 실수하지 않습니다", tone="r", cs=30),
            dict(key="6 Legend", text="최적의 주행을 | 전력으로 합니다", tone="r", cs=30)], size=40, row_gap=22)
    c.stage_video("levels")
    note = wrap("기본 선택은 2 Rookie.\n단계가 오를수록 | AI가 빨라지고 | 스튜어드도 엄격해집니다", STAGE_W, 42)
    c.report.append("  [note] " + lines_text(note).replace(NL, "  /  "))
    c.add(f'<div id="{cid}-n" class="txt" style="left:{STAGE_X}px;top:640px;font-size:42px;line-height:58px;opacity:0">'
          + html_lines(note) + '</div>', f'IN("#{cid}-n", 4.0, {{}}, 0.8);')
    c.t_end = max(c.t_end, 15.0)
    return c


# ---- settings: the game's own rows ------------------------------------------------------------
def c_set_open(cid):
    c = Card(cid, "설정", "열기")
    c.rows([dict(key="열기", text="메인 메뉴의 SETTINGS", tone="y"),
            dict(key="저장", text="값은 저장되어 | 다음에도 유지됩니다", tone="d"),
            dict(key="경기 중", text="일시정지 카드의 SETTINGS에서 | 일부 항목만 바꿀 수 있습니다", tone="g"),
            dict(key="시작 전", text="나머지는 세션을 | 시작하기 전에만 바꿀 수 있습니다", tone="r")])
    c.stage_video("settings")
    return c


def blocks(c: Card, items, y0=150, t0=1.6, size=42):
    """Settings blocks stacked full width; returns the y under the last one and the times."""
    ts = c.row_times([it["text"] for it in items], t0)
    y = y0
    for k, it in enumerate(items):
        y = c.setting(k, int(y), it["sel"], it["text"], ts[k], it.get("tag", ""), it.get("tone", "g"), size=size,
                      tag_w=it.get("tag_w", 300))
        y += 34
    return y, ts


def c_set_auto(cid):
    c = Card(cid, "설정", "자동 주행")
    y, ts = blocks(c, [dict(sel=2, text="레이싱 라인을 따라 차가 스스로 조향합니다.\n직접 조향하면 내가 우선입니다",
                            tag="매우 어려울 때만 ON", tone="y", tag_w=420),
                       dict(sel=3, text="가속과 브레이크도 자동입니다.\n앞차와 간격을 지키고 직접 밟는 동안은 내가 우선입니다",
                            tag="매우 어려울 때만 ON", tone="y", tag_w=420)])
    t = ts[-1] + 3.0
    note = wrap("직접 조종할 일이 거의 없어 지루합니다.\n조작이 매우 어렵게 느껴질 때만 켜세요.", 1500, 42)
    c.report.append("  [note] " + lines_text(note).replace(NL, "  /  "))
    c.add(f'<div id="{cid}-n" class="abs" style="left:96px;top:{int(y) + 4}px;width:1728px;opacity:0">'
          f'<div class="chip lat" style="left:0;top:4px;width:150px;height:56px;line-height:56px;font-size:32px;background:{RED};color:#fff">비추천</div>'
          f'<div class="txt" style="left:180px;top:0;font-size:42px;line-height:58px">' + html_lines(note) + '</div></div>',
          f'IN("#{cid}-n", {t:.1f}, {{ y: 20 }}, 0.9);')
    c.add(f'<div id="{cid}-n2" class="txt" style="left:102px;top:{int(y) + 4 + 130}px;font-size:36px;color:{GREY};opacity:0">계획이 있는 서킷에서만 켜집니다</div>',
          f'IN("#{cid}-n2", {t + 2.0:.1f}, {{}}, 0.8);')
    c.t_end = max(c.t_end, t + 7.5)
    return c


def c_set_wall(cid):
    c = Card(cid, "설정", "트랙 리밋 월")
    t = c.row_times(["흰 선에 보이지 않는 벽이 생깁니다 네 바퀴가 모두 나가려 하면 막혀서 선을 따라 미끄러집니다 트랙 이탈이 일어나지 않습니다 내 차에만 적용되고 경기 중에도 바꿀 수 있습니다"])
    c.setting(0, 150, 6, "흰 선에 보이지 않는 벽이 생깁니다.\n네 바퀴가 모두 나가려 하면 | 막혀서 선을 따라 미끄러집니다.\n트랙 이탈이 일어나지 않습니다.\n내 차에만 적용되고 | 경기 중에도 바꿀 수 있습니다.",
              t[0], "초심자 ON 추천", "g", text_w=880, tag_w=330, tag_below=True)
    c.stage_video("wall", y=290, h=446, t=t[0] + 0.5)
    c.t_end = max(c.t_end, t[0] + 13.0)
    return c


def c_set_aids(cid):
    c = Card(cid, "설정", "차량 보조")
    y, ts = blocks(c, [dict(sel=7, text="가속할 때 뒷바퀴가 | 헛도는 것을 막아 줍니다", tag="끄지 마세요", tone="r"),
                       dict(sel=8, text="브레이크가 | 잠기는 것을 막아 줍니다", tag="끄지 마세요", tone="r"),
                       dict(sel=9, text="미끄러질 때 | 반대 조향을 대신해 줍니다", tag="기본 ON", tone="d")], size=40)
    t = ts[-1] + 3.0
    note = wrap("TC와 ABS는 | 시뮬레이션 레이싱에 익숙하신 분들만 끄세요.\n끄면 차를 제어하기가 매우 어렵습니다.", 1500, 42)
    c.report.append("  [note] " + lines_text(note).replace(NL, "  /  "))
    c.add(f'<div id="{cid}-n" class="abs" style="left:96px;top:{int(y) + 4}px;width:1728px;opacity:0">'
          f'<div class="chip lat" style="left:0;top:4px;width:150px;height:56px;line-height:56px;font-size:32px;background:{RED};color:#fff">주의</div>'
          f'<div class="txt" style="left:180px;top:0;font-size:42px;line-height:58px">' + html_lines(note) + '</div></div>',
          f'IN("#{cid}-n", {t:.1f}, {{ y: 20 }}, 0.9);')
    c.t_end = max(c.t_end, t + 5.5)
    return c


def c_set_line(cid):
    c = Card(cid, "설정", "레이싱 라인")
    t = c.row_times(["도로 위에 최적의 라인이 표시됩니다 초록은 가속 노랑은 감속 시작 빨강은 브레이킹입니다"])
    c.setting(0, 150, 4, "도로 위에 최적의 라인이 표시됩니다.\n[[초록]]은 가속, | [[노랑]]은 감속 시작, | {{빨강}}은 브레이킹입니다",
              t[0], "숙련자가 아니면 ON", "g", text_w=880, tag_w=330, tag_below=True)
    c.stage_video("rline", y=290, h=446, t=t[0] + 0.5)
    c.t_end = max(c.t_end, t[0] + 11.0)
    return c


def c_set_gear(cid):
    c = Card(cid, "설정", "수동 변속")
    t = c.row_times(["변속을 직접 합니다 회전 한계에서 출력이 끊깁니다 AUTO PEDALS가 켜져 있으면 적용되지 않습니다"])
    c.setting(0, 150, 0, "변속을 직접 합니다. | 회전 한계에서 출력이 끊깁니다.\nAUTO PEDALS가 켜져 있으면 | 적용되지 않습니다",
              t[0], "숙련자가 아니면 OFF 강추", "r", text_w=880, tag_w=430, tag_below=True)
    c.stage_video("shift", y=290, h=446, t=t[0] + 0.5)
    c.t_end = max(c.t_end, t[0] + 11.0)
    return c


def c_set_misc(cid):
    c = Card(cid, "설정", "그 밖의 항목")
    blocks(c, [dict(sel=1, text="값이 클수록 휠을 덜 돌려도 같은 조향이 됩니다.\n0.5x ~ 4.0x", tag="휠 전용", tone="d"),
               dict(sel=5, text="락업 · 슬립 · 휠스핀 때 | 도로에 타이어 자국이 남습니다", tag="기본 ON", tone="d"),
               dict(sel=14, text="앞차 뒤에서 | 공기저항이 줄어듭니다", tag="기본 ON", tone="d")])
    return c


def c_set_rules(cid):
    c = Card(cid, "설정", "규칙 옵션")
    y, ts = blocks(c, [dict(sel=10, text="멈춘 차가 옐로를 내고 모두 감속합니다.\n끄면 옐로 자체가 없습니다", tag="기본 ON", tone="d"),
                       dict(sel=11, text="출발 신호 중 스로틀을 밟으면 5초입니다.\n끄면 페널티가 없습니다", tag="초심자 OFF 추천", tone="g")])
    top = int(y) + 6
    h = 1048 - top                              # a whole 16:9 picture, never a cropped strip
    w = int(h * 16 / 9)
    gap = 48
    c.stage_video("yellow", y=top, h=h, x=96, w=w, vid_idx=0, t=ts[-1] + 1.0)
    c.stage_video("lights", y=top, h=h, x=96 + w + gap, w=w, vid_idx=1, t=ts[-1] + 2.0)
    c.report.append(f"  videos {w} x {h} at y={top}")
    c.t_end = max(c.t_end, ts[-1] + 9.0)
    return c


def c_set_drs(cid):
    c = Card(cid, "설정", "DRS")
    y, ts = blocks(c, [dict(sel=12, text="앞차 1.8초 이내의 직선에서 | 공기저항이 줄어듭니다", tag="재미로 ON 추천", tone="g"),
                       dict(sel=13, text="DRS 표시가 깜빡이면 직접 엽니다.\n브레이크를 밟으면 닫힙니다", tag="재미로 ON 추천", tone="g")])
    top = int(y) + 6
    fh = c.still("drs_ready.png", DRS_HUD, 96, top, 840, ts[-1] + 1.0, native=1600, name="s0")
    c.still("drs_open.png", DRS_HUD, 984, top, 840, ts[-1] + 2.0, native=1600, name="s1")
    c.add(f'<div id="{cid}-l0" class="txt" style="left:100px;top:{top + fh + 14}px;font-size:38px;color:{GREY};opacity:0">DRS 표시가 깜빡이면</div>'
          f'<div id="{cid}-l1" class="txt" style="left:988px;top:{top + fh + 14}px;font-size:38px;color:{GREEN};opacity:0">누르면 DRS OPEN</div>',
          f'IN("#{cid}-l0", {ts[-1] + 1.4:.1f}, {{}}, 0.6); IN("#{cid}-l1", {ts[-1] + 2.4:.1f}, {{}}, 0.6);')
    c.t_end = max(c.t_end, ts[-1] + 8.5)
    return c


def c_set_pen(cid):
    c = Card(cid, "설정", "페널티")
    y, ts = blocks(c, [dict(sel=15, text="이탈 경고와 시간 페널티", tag="초심자 OFF 추천", tone="g"),
                       dict(sel=16, text="충돌 경고와 시간 페널티", tag="초심자 OFF 추천", tone="g"),
                       dict(sel=17, text="옐로 위반 경고와 시간 페널티", tag="초심자 OFF 추천", tone="g")])
    c.add(f'<div id="{cid}-n" class="txt" style="left:100px;top:{int(y) + 8}px;font-size:46px;opacity:0">'
          + mk("끄면 그 종류는 [[경고도 페널티도]] 없습니다") + '</div>',
          f'IN("#{cid}-n", {ts[-1] + 2.2:.1f}, {{}}, 0.8);')
    c.t_end = max(c.t_end, ts[-1] + 7.0)
    return c


def c_poster(cid):
    c = Card(cid, "추천 세팅", "처음이라면", dur=26.0)
    TONE_W = ("#e8e8ee", INK)
    cols = [[("켜세요", "g", [("TRACK-LIMIT WALL", "초심자"), ("RACING LINE", "숙련자가 아니면"),
                           ("DRS", "재미로"), ("MANUAL DRS", "재미로")])],
            [("끄세요", "y", [("MANUAL GEARBOX", "숙련자가 아니면"), ("JUMP START PENALTY", ""),
                           ("PENALTY · TRACK LIMITS", ""), ("PENALTY · COLLISIONS", ""),
                           ("PENALTY · YELLOW FLAGS", "")])],
            [("끄지 마세요", "r", [("TRACTION CONTROL", ""), ("ABS", "")]),
             ("매우 어려울 때만 켜세요", "w", [("AUTO STEERING", ""), ("AUTO PEDALS", "")])]]
    colw, gap = 560, 24
    t0 = 1.4
    for ci, groups in enumerate(cols):
        x = 96 + ci * (colw + gap)
        y = 160
        for gi, (head, tone, names) in enumerate(groups):
            bg, fg = TONE_W if tone == "w" else TONE[tone]
            c.add(f'<div id="{cid}-h{ci}_{gi}" class="chip lat" style="left:{x}px;top:{y}px;width:{colw}px;height:84px;line-height:84px;'
                  f'font-size:{46 if tone != "w" else 40}px;background:{bg};color:{fg}">{head}</div>',
                  f'IN("#{cid}-h{ci}_{gi}", {t0 + ci * 3.0 + gi * 1.5:.2f}, {{ y: 20 }}, 0.8);')
            y += 108
            for k, (name, note) in enumerate(names):
                sub = (f'<div class="abs" style="left:26px;top:46px;font-size:28px;color:{GREY};font-weight:700;white-space:nowrap">{note}</div>'
                       if note else "")
                c.add(f'<div id="{cid}-n{ci}_{gi}_{k}" class="abs" style="left:{x}px;top:{y}px;width:{colw}px;height:84px;opacity:0">'
                      f'<div class="abs" style="left:0;top:0;width:8px;height:78px;background:{RED}"></div>'
                      f'<div class="abs lat" style="left:26px;top:{2 if note else 16}px;font-size:38px;line-height:44px;white-space:nowrap">{name}</div>'
                      f'{sub}</div>',
                      f'IN("#{cid}-n{ci}_{gi}_{k}", {t0 + ci * 3.0 + gi * 1.5 + 0.5 + k * 0.45:.2f}, {{ x: 30 }}, 0.7);')
                y += 102
            y += 24
    c.add(f'<div id="{cid}-same" class="txt" style="left:96px;top:818px;font-size:36px;color:{GREY};opacity:0">'
          f'그대로 두세요 · STEERING ASSIST · TYRE MARKS · YELLOW FLAGS · SLIPSTREAM</div>',
          f'IN("#{cid}-same", 12.0, {{}}, 0.8);')
    note = wrap("숙련자라면 | RACING LINE을 끄고 | MANUAL GEARBOX를 켜셔도 됩니다.\nTC · ABS는 시뮬레이션 레이싱에 | 익숙하신 분들만 끄세요.", 1728, 44)
    c.report.append("  [note] " + lines_text(note).replace(NL, "  /  "))
    c.add(f'<div id="{cid}-note" class="txt" style="left:96px;top:902px;font-size:44px;line-height:62px;opacity:0">'
          + html_lines(note) + '</div>', f'IN("#{cid}-note", 14.0, {{ y: 20 }}, 0.9);')
    return c


# ================================================================ the plan
PLAN = [
    ("", c_open), ("세션", c_sessions),
    ("퀄리파잉", c_q_flow), ("퀄리파잉", c_q_invalid), ("퀄리파잉", c_q_result),
    ("그랑프리", c_gp_grid), ("그랑프리", c_gp_start), ("그랑프리", c_gp_jump), ("그랑프리", c_gp_finish),
    ("스튜어드", c_rc_overview), ("스튜어드", c_tl), ("스튜어드", c_collision), ("스튜어드", c_yellow),
    ("추월", c_drs), ("추월", c_slip),
    ("난이도", c_levels),
    ("설정", c_set_open), ("설정", c_set_auto), ("설정", c_set_line), ("설정", c_set_wall), ("설정", c_set_aids),
    ("설정", c_set_gear),
    ("설정", c_set_misc), ("설정", c_set_rules), ("설정", c_set_drs), ("설정", c_set_pen),
    ("추천", c_poster),
]


def wipes(seams):
    items, js = [], []
    for k, seam in enumerate(seams):
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


def index(hosts, total):
    return f"""<!doctype html>
<html lang="ko">
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
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total:.2f}" data-width="1920" data-height="1080">
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
    copy_stills()
    check_glitches()
    if "--cuts" in sys.argv or not (HERE / "assets" / "footage" / "msg_windows.json").exists():
        subprocess.run([sys.executable, str(HERE / "msgscan.py")], check=True)
    (HERE / "compositions").mkdir(exist_ok=True)
    for old in (HERE / "compositions").glob("c*.html"):
        old.unlink()
    hosts, t, seams, prev_ch, starts = [], 0.0, [], None, {}
    for k, (chapter, fn) in enumerate(PLAN):
        cid = f"c{k:02d}"
        card = fn(cid)
        html = card.build()
        if prev_ch is not None and chapter != prev_ch:
            seams.append(t)
        prev_ch = chapter
        (HERE / "compositions" / f"{cid}.html").write_text(html, encoding="utf-8")
        hold = "" if cid == "c00" else " data-hold"
        hosts.append(f'''    <div id="{cid}" data-composition-id="{cid}" data-composition-src="compositions/{cid}.html"
      data-start="{t:.2f}" data-duration="{card.dur}" data-track-index="1" data-width="1920" data-height="1080" data-track-kind="graphics"{hold}></div>''')
        starts[cid] = (round(t, 2), card.dur, fn.__name__)
        print(f"{cid} {fn.__name__:14s} {t:6.1f} s  {card.dur:5.1f}")
        for line in card.report:
            print(line)
        t += card.dur
    hosts.append(f'''    <div id="wipes" data-composition-id="wipes" data-composition-src="compositions/wipes.html"
      data-start="0" data-duration="{t:.2f}" data-track-index="9" data-width="1920" data-height="1080" data-track-kind="graphics" style="z-index:50"></div>''')
    (HERE / "compositions" / "wipes.html").write_text(wipes(seams), encoding="utf-8")
    (HERE / "index.html").write_text(index(hosts, t), encoding="utf-8")
    (HERE / "timeline.json").write_text(json.dumps(starts, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"built {len(PLAN)} cards, {t:.1f} s ({t / 60:.1f} min), seams {[round(s, 1) for s in seams]}")


if __name__ == "__main__":
    main()
