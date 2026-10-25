# promo — 부스 소개 영상

AI-SW 축전 부스에서 반복 재생하는 소개 영상의 소스. 영상 한 편 = 폴더 하나.

| 폴더 | 내용 |
|---|---|
| `footage/` | 게임을 직접 돌려 찍은 원본 클립(mp4)과, 클립마다 프레임별 입력 로그(json). `tools/shoot.py`가 만든다. 용량이 커서(수백 MB) 저장소에 올리지 않는 편이 낫다. |
| `moza_guide/` | MOZA R3 조작법 영상 (HyperFrames 프로젝트). `build.py`가 `index.html`과 `compositions/*.html`을 만든다. |
| `rules_guide/` | 규칙·난이도·설정 소개 영상(약 6분, 카드 27장). 그림은 모두 인게임 영상·스크린샷이고(직접 그린 도형 없음), `kit.py`(카드 부품·줄바꿈)와 `build.py`(문구·카드 순서)로 만든다. 규칙 수치는 `racecontrol.py` · `field.py` · `rules.py` · `teams.py` · `settings.py`에서 옮긴 것이라 코드를 바꾸면 `build.py`의 문구도 맞춘다. |
| `shoot_all.sh` | moza_guide에 쓰는 장면을 1080p로 한 번에 다시 찍는다. |

## 장면 찍기

```bash
cd ai_sw
./.venv/Scripts/python.exe tools/shoot.py --list                       # 장면 목록
./.venv/Scripts/python.exe tools/shoot.py pedals_t1 --out promo/footage   # 1080p, 30fps
./.venv/Scripts/python.exe tools/shoot.py reset --size 640 360 --no-video --shots 2 4   # 빠른 확인용
```

게임이 화면 없이(offscreen) 돌고, 시계가 프레임당 정확히 1/30초씩 흐르므로 GPU가 느려도 영상은 매끈하다.
휠은 가상 MOZA다: 장면 스크립트가 조향·페달·버튼 비트를 정하고, 게임의 `wheel.poll_keys`가 그것을 키로 바꾼다.
`<장면>.json`에는 차가 실제로 받은 입력(steer/thr/brk)과 눌린 버튼이 프레임마다 남는다.
새 장면은 `tools/shoot.py`의 `@scene(...)` 함수 하나로 추가한다.

장면은 `tools/shoot.py`에 있다. 규칙 영상용: `q_flow` `q_invalid` `q_finish` `gp_start` `gp_jump` `gp_limits` `gp_yellow` `rline` `settings_scroll` `grid_menu`.
그랑프리 장면은 필드 워커 프로세스와 같이 도는데도 고정 시계로 매끈하게 찍힌다(`gp_probe`로 확인).
조향 스크립트(`follow()`)로 트랙 리밋 경고·흑백 깃발·페널티를 실제 게임에서 일으켜 찍는다.

## 영상 만들기 (moza_guide)

```bash
cd ai_sw/promo/moza_guide
../../.venv/Scripts/python.exe build.py            # 필요한 구간 자르기 + html 생성 (--cuts: 구간 다시 자르기)
npx hyperframes check                              # 규칙·레이아웃 검사
npx hyperframes preview --background               # Studio 미리보기
npx hyperframes render --quality delivery --video-frame-format png -o renders/moza_r3_guide.mp4
```

- 글자·위치·타이밍은 `build.py`와 `SPEC.md`에 있다. 생성된 html을 직접 고치면 다음 build에서 지워진다.
- 휠·페달 사진은 mozaracing.com R3 번들 페이지의 제품 이미지를 배경 제거해 `assets/img/`에 둔 것이다.
- 조작 설명의 출처는 `game/menu.py`의 `CONTROL_TABS["MOZA WHEEL"]`와 `game/wheel.py`다. 조작을 바꾸면 `build.py`의 문구도 같이 고친다.

## 규칙 영상 (rules_guide)

```bash
cd ai_sw/promo/rules_guide
../../.venv/Scripts/python.exe build.py            # 구간 자르기(없는 것만) + 메시지 구간 감지 + 카드 html (--cuts: 다시 자르기)
npx hyperframes check
npx hyperframes snapshot --at 62,110,250           # 카드 확인 (timeline.json에 카드별 시작·길이)
npx hyperframes render --quality delivery --video-frame-format png -o renders/formula_ai_rules_guide.mp4
```

- 카드 하나 = `build.py`의 함수 하나(`PLAN` 순서). 카드 길이는 글 읽는 시간으로 자동 계산된다(`kit.Card.row_times`).
- 문체는 -습니다 또는 명사구. 문구는 `" | "`로 구를 나눠 쓰면 줄바꿈이 그 사이에서만 일어난다(`kit.wrap`). 빌드 로그에 줄바꿈 결과가 그대로 찍히니 어색한 곳을 먼저 거기서 본다.
- 설정 화면은 실제 스크린샷(`assets/settings/row_NN.png`, `settings_scroll` 장면)에서 해당 행만 잘라 쓴다.
- RACE CONTROL 메시지 확대는 `msgscan.py`가 영상에서 메시지가 떠 있는 구간을 찾아 그때만 보여 준다.
- 새로 찍은 장면: `tl_wheels`(흰 선과 바퀴, 자유 카메라) `lights_cam`(출발 신호, 자유 카메라) `slip_gp`(슬립스트림) `collide`(충돌). 자유 카메라는 `shoot.free_cam()` + `shoot.aim()`.
- 메뉴 화면을 찍을 때 간헐적으로 반쯤 그려진 프레임이 섞이면 장면에 `rerender=True`를 준다(촬영 전 한 번 더 그리고 GPU를 기다린다). `glitch.py scan 클립.mp4`로 확인하고, `build.py`는 메뉴 클립에 깨진 프레임이 있으면 경고한다.
- 카드에 영상을 넣을 때는 16:9 전체가 보이게 크기를 계산한다(잘린 띠 금지).
