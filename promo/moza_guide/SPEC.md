# SPEC — MOZA R3 조작법 (FORMULA-AI booth loop)

GOAL:   A stranger at the booth, who has never held a wheel, knows after one pass that the wheel
        steers, the right pedal is the gas, the left pedal is the brake, and which five buttons
        matter (A, X, CAM, CAM+A, the D-pad). It loops on a screen with no sound, so nothing
        depends on audio and any paused frame reads.
OUTPUT: HyperFrames, 1920 x 1080, 30 fps, silent, Korean. 74.5 s = 2235 frames.
TIME:   S1 hook 0-6 | S2 start (D-pad, A) 6-14 | S3 pedals 14-24.5 | S4 wheel 24.5-35.5 |
        S5 six buttons 35.5-62.5 | S6 cheat sheet 62.5-74.5 (held 8 s, the frame a passer-by catches).

LOOK:   The game's own broadcast chrome, not a template. Ground: real game footage (S1, S3, S4) or
        INK #15151e (S2, S5, S6). Cards: PANEL #1f1f2b with a 8 px RED #e10600 top band, square
        corners, no shadow. Edges that cut: a 12 deg slash (the logo's), never a pill.
        Ink on dark: WHITE #ffffff / GREY #969aa2. One focus colour for "press this": YELLOW
        #f0d430 (game's own yellow) on rings and key chips. Data colours as the HUD: THROTTLE
        GREEN #3cd25a, BRAKE RED #e10600.
        Faces: Bahnschrift 700 for keys and numerals (the game's HUD face), Noto Sans KR 900 for
        Korean headlines (68-96 px), 700 for sub lines (44 px). Min text 44 px.
        Product: the MOZA photos from mozaracing.com, cut out, never recoloured.
OBJECTS: The wheel is the ES wheel in the R3 bundle (buttons A B X Y, D-pad, CAM, paddles,
        LED bar). The pedals are the SR-P Lite. Both are the manufacturer's photos; the overlay
        boxes sit on the real buttons (measured on the 1000 px photo).
MOTION: enter: power4.out 0.5-0.7 s, words blur 6 -> 0 px 0.13 s, 0.08 s apart. Seams: red slash
        wipe 0.9 s power3.inOut, cut under full cover at the seam. Telemetry (wheel angle, pedal
        fill, button flashes) is the game's own log of what the car received, 15 Hz, linear.
SHOTS:  see SCENES in build.py (one line per shot, with the footage window).
BANNED: glow, floating particles, glass cards, dark void with small things in it, pill chips,
        mono caps labels, logo in a corner, bullets, three cards in a row, idle wobble, creeping
        scale, text that moves after landing, any claim not in game/menu.py CONTROL_TABS.
QA:     snapshot at each seam +-0.6 s, at S3 7.0 s (accel -> brake), S4 6 s (wheel at full lock),
        S5 each step midpoint, S6 at 2 s and 10 s. Every label sits on its own button.

SOURCE OF TRUTH FOR CONTROLS: ai_sw/game/menu.py CONTROL_TABS["MOZA WHEEL"] and
ai_sw/game/wheel.py (KEY_BUTTONS, LAYER_KEYS). Settings gates: paddles need MANUAL GEARBOX, Y
needs MANUAL DRS (both under SETTINGS), both off by default.
