# FORMULA-AI

직접 운전하는 3D 레이싱 게임. 20대의 AI와 같은 그리드에서 출발하는 그랑프리와, 기록을 겨루는 퀄리파잉이 있다.
서킷은 실제 F1 서킷 23개(데이터: f1tenth_racetracks). 전체 방향은 [CONCEPT.md](CONCEPT.md).

## 실행

**`FORMULA-AI.bat`** 을 더블클릭하면 끝이다. 처음 한 번은 Python(3.10~3.12, 없으면 설치)과 `.venv`와 패키지를
알아서 준비하고(인터넷 필요), 이후엔 바로 게임이 열린다. 환경만 따로 만들거나 고치려면 `setup.bat`, 메뉴 없이
바로 시작하려면 `run.bat --track Spa --laps 5`.

직접 실행하려면:

```bash
python run.py            # 메뉴에서 모드·서킷 선택
python run.py --track Spa --laps 5   # 메뉴 건너뛰고 그랑프리
```

옵션: `--mode quali|gp`, `--track <이름>`, `--laps <n>`, `--fullscreen`, `--mute`.
요구사항: OpenGL 3.2 이상. 1080p 20대 그랑프리에서 80 fps 안팎(내장 그래픽은 더 낮음).

## 조작

| 키 | 동작 |
|---|---|
| W/↑, S/↓ | 가속, 브레이크·후진 |
| A D / ← → | 조향 |
| SPACE | 핸드브레이크 |
| R | 트랙 위로 리셋 |
| C | 카메라 (온보드 / 체이스 / 원거리) |
| X | 뒤 보기 |
| G / SHIFT+G | 관전: G는 다음 차로, SHIFT+G는 내 차로 바로 복귀 (퀄리파잉은 고스트 토글) |
| TAB | 타워 간격: 앞차 ↔ 선두 |
| T | 주행 보조(TC·ABS·조향) on/off |
| H / P | HUD / 일시정지 카드 숨기기 |
| F / M | 자유 카메라 / 음소거 |
| F11 | 전체화면 |
| F9 | (녹화는 현재 비활성: config.REC_ENABLED) |
| ESC | 일시정지 |

## 게임 모드

- **퀄리파잉**: 혼자 달려 기록을 낸다. 같은 난이도 AI 19명의 예선 기록이 타워에 나오고, 폴 드라이버의 랩이 고스트로 달린다.
- **그랑프리**: 플레이어 1 + AI 19(10팀 × 2)가 소등 출발한다. 접촉, 슬립스트림, DRS, 옐로 플래그(모든 차 100 km/h 이하),
  스튜어드 판정(트랙 리밋·접촉 페널티)이 모두 작동한다. 출발 순위는 서킷 선택 화면에서 A/D로 고른다.
- **난이도 1–6** (`game/teams.py`): 낮은 그립으로 푼 계획을 따라 달리게 해 단계를 만든다. 모나 폴 랩타임:
  Novice 99.8 s, Rookie 96.3, Amateur 94.1, Club 92.1, Pro 89.2, Legend 88.1.

## 설정 (메인 메뉴의 SETTINGS 버튼 · `O` · 일시정지 카드의 SETTINGS)

값은 `~/.formula-ai/settings.json`에 저장된다. 경기 중(ESC → SETTINGS)에는 수동 변속, 조향 감도, TC·ABS·조향 보조만
바꿀 수 있고(나머지는 흐리게 표시), 규칙·자동 주행 항목은 세션을 시작하기 전에만 바꾼다. 항목이 많아 W/S로 스크롤된다.

| 항목 | 효과 |
|---|---|
| RACING LINE | 최적해(최고 그립 계획)의 레이싱 라인을 도로 위 홀로그램으로 표시. 초록 = 가속, 노랑 = 감속 시작, 빨강 = 브레이킹(강할수록 진함), 가로 막대 = 브레이킹 포인트. 경기 중에도 켜고 끌 수 있다 |
| TYRE MARKS | 락업·슬라이드·휠스핀 때 도로에 검은 타이어 자국을 남긴다(경기 중에도 켜고 끌 수 있다) |
| TRACTION CONTROL, ABS, STEERING ASSIST | 운전 보조. 경기 중 `T`는 셋을 한꺼번에 켜고 끈다 |
| MANUAL GEARBOX, STEERING SENSITIVITY | 수동 변속(휠 패들 / E·Z), 휠 감도 0.5x~4.0x |
| AUTO STEERING | 조향을 추종기가 맡는다. A / D를 누르고 있는 동안은 내 조향이 우선이고, 놓으면 추종기가 그 자리의 선을 이어서 따라간다(레이싱 라인으로 되돌아가지 않고, 흰 선 밖으로는 나가지 않게). Q는 레이싱 라인으로 복귀 |
| AUTO PEDALS | 가속·브레이크도 자동(W / S를 누르는 동안은 내 입력이 우선). 선택한 난이도의 중위권 AI와 같은 계획과 페이스로 달리고(Novice는 느리고 조심스럽게, Legend는 전력으로), 앞차 뒤에서는 간격을 유지. 조향까지 켜면 옆으로 움직이는 입력만으로 주행한다 |
| YELLOW FLAGS | 멈춘 차가 옐로를 내고 모두 감속. 끄면 옐로 자체가 없다 |
| JUMP START PENALTY | 불이 켜져 있는 동안 스로틀을 밟으면(AI는 가끔 성급하게 반응) 5초 페널티. 어느 쪽이든 차는 불이 꺼질 때까지 출발하지 못한다. AI의 스타트 반응은 드라이버마다, 레이스마다 다르다 |
| DRS, SLIPSTREAM | 각각 끌 수 있다. MANUAL DRS를 켜면 DRS 구간·간격 조건이 맞을 때 하단 중앙에 `DRS` 표시가 깜빡이고, `V`(휠·패드는 레이스 중 Y)로 열고 닫는다. 열리면 `DRS OPEN`(초록, 시야각 확장), 브레이크를 밟거나 구간을 벗어나면 닫힘. 타워에는 DRS가 표시되지 않는다 |
| PENALTY · TRACK LIMITS / COLLISIONS / YELLOW FLAGS | 종류별로 시간 페널티(경고 포함)를 끈다 |

자동 조향·페달은 계획 해가 있는 서킷에서만 켜진다.

## AI는 어떻게 달리나

한 대의 AI는 네 층이다. 위로 갈수록 판단, 아래로 갈수록 손발.

1. **계획** — 서킷마다 차 물리 모델로 푼 최소 랩타임 최적해(`tools/mintime.py`, CasADi + IPOPT, 그립 0.97–0.74 일곱 단계).
   어디로 얼마나 빨리 달릴지의 기준선이다. 모나 해 88.08 s, 게임 물리로 주행하면 88.05 s.
2. **판단 층** (`game/racecraft.py`, `game/raceai.py`) — 0.13초마다 라인 오프셋과 페이스를 정한다. 추월, 수비, 합류, 줄서기.
   **학습된 정책**이 기본이다(`config.RACE_AI = "rl"`, 가중치 `assets/policies/raceai.npz`, numpy 추론).
   파일이 없거나 `"rules"`이면 손으로 짠 규칙이 결정한다.
3. **추종** (`game/drivenet.py`, `game/mintime_driver.py`) — 오프셋과 속도 제한을 받아 조향과 페달을 만든다.
   **작은 신경망**이 기본이다(`config.DRIVE_AI = "rl"`, 가중치 `assets/policies/drivenet.npz`). 이 망은 앞차와 옆차를 직접 본다
   (입력 57개). 앞차 간격 제한, 옆차 간격 보정, 차선 곡률 제한 같은 규칙은 이 망에 적용하지 않고, 앞차에 붙지 않고 옆차를 피하는
   일은 망이 스스로 한다. 규칙 전체를 따르는 손으로 만든 제어기를 교사로 모방(DAgger)한 뒤 PPO로 접촉, 코스 이탈, 연석,
   입력의 거칠기를 줄였다. 주변 차를 보지 못하는 이전 세대 망(입력 48개)을 넣으면 예전처럼 규칙 아래에서 달린다.
   `"rules"`로 바꾸면 손으로 만든 제어기가 직접 운전한다.
4. **규칙** — 옐로 플래그 속도 제한, 갇혔을 때의 복구, 출발 신호, 레이스 컨트롤(스튜어드)만 남는다.
   앞차 간격과 옆차 간격은 규칙이 아니라 망이 맡는다(규칙 운전을 고르면 규칙이 다시 켜진다).

기본 설정에서는 2·3층을 **하나의 신경망**(`game/onenet.py`, `config.ONE_AI = "rl"`, 가중치 `assets/policies/onenet.npz`)이 맡는다.
트렁크 하나에 머리가 둘이다: 빠른 머리(조향·페달, 0.033초마다)와 느린 머리(차선·페이스·추월 시작, 0.13초마다). 입력은 운전 관측과
판단 관측을 합친 117개다. 두 망(위 2·3층)을 교사로 모방(DAgger)해 시작점을 만들고, 두 머리를 각자의 보상으로 한꺼번에 PPO로
다듬었다(`tools/dagger_one.py`, `tools/ppo_one.py`). `ONE_AI = "off"`로 바꾸면 두 망 구성으로 돌아간다.

판단 층 학습 결과 (8개 서킷 × 8시드, 정책이 한 대만 운전): 장면 평균 성공률 규칙 0.71 → 학습 0.73(오차 범위),
20대 3랩 레이스에서 복구 22.8 → 14.5회, 뒤차 쪽 차선 이동 122 → 70회/랩, 추월 15.0 → 10.0회/랩.

조향·페달·판단 결과 (20대 3랩 레이스, 서킷 8개 × 시드 4개 = 32판의 평균):

| | 규칙 운전 (8판) | 두 망 | 하나의 신경망 (지금) |
|---|---|---|---|
| 접촉 / 레이스 | 18.1 | 9.8 | 5.2 |
| 시간 페널티 (초 / 레이스) | 38.4 | 25.5 | 10.9 |
| 복구 / 레이스 | 14.5 | 8.7 | 4.1 |
| 추월 / 랩 | 10.0 | 8.6 | 6.5 |
| 앞차 30 m 이내에서 느리게 막힌 시간 | — | 5.5 % | 4.1 % |

시드가 다른 두 묶음(16판씩)에서 같은 방향으로 나왔다. 추월은 줄었다. 규칙 운전과 두 망의 추월 수에는 접촉과 이탈로 생긴 순위 변동도
섞여 있어, 줄어든 만큼이 깨끗한 추월의 감소는 아니다.

**알려진 문제**: 추월 횟수가 두 망 구성보다 약 25 % 적다. 막힌 시간은 줄었지만 0은 아니다. 학습 중 평가는 점수가 크게 흔들리고
(체크포인트마다 접촉이 2~11회로 달랐다), 그래서 점수가 높은 체크포인트를 골라 시드가 다른 레이스로 다시 확인했다.

### 학습·평가 도구

| 명령 (`ai_sw/`에서) | 하는 일 |
|---|---|
| `tools/race_metrics.py [--policy X.npz] [--races N] [--all-cars] --name X` | 5개 장면과 전체 레이스 지표 (성공률, 접촉, 복구, 연석, 줄서기). 결과 `policy/raceai/X.json` |
| `tools/ppo_raceai.py --bc policy/raceai/bc.npz --out DIR --jobs 4` | 판단 층 PPO |
| `tools/dagger_drive.py train --out DIR --jobs 4` / `eval --net X.npz` | 조향·페달 신경망 DAgger 학습 / 교사와 비교 평가 |
| `tools/ppo_drive.py --init DIR/dagger_last.npz --out DIR2` | 신경망 주행 PPO(연석·이탈·부드러움 보상) |
| `tools/dagger_one.py train --out DIR` / `tools/ppo_one.py --init DIR/one_last.npz --out DIR2` | 하나의 신경망: 두 망을 모방 / 두 머리를 함께 PPO |
| `tools/solve_all.py` | 모든 서킷의 계획 계산 (한 번에 한 서킷, 메모리 큼) |
| `tools/race_sim.py`, `tools/smoke_raceai.py`, `tools/smoke_headless.py` | 화면 없는 레이스 시뮬, 배관 점검, 물리 검증 |
| `../ai_sw_gpu/kaggle/raceai_push.py NAME [--trainer dagger]` | Kaggle CPU 커널로 학습 (4코어, 로그는 끝난 뒤에 받을 수 있음) |

Windows에서는 학습 작업자를 4개까지만 쓴다(그 이상은 메모리 오류).

## 구조

```
run.py, run.bat        게임 시작
game/
  config.py            튜닝 상수 전부
  vehicle.py           동역학 자전거 모델 (Pacejka 타이어, 마찰원, 하중이동, TC/ABS/ESC)
  surface.py trackdata.py trackmesh.py scenery.py props.py terrain.py   트랙·노면·경치
  mintime_driver.py    계획과 추종 제어기
  racecraft.py raceai.py raceenv.py   판단 층 (규칙 / 학습 정책) 과 학습용 환경·심판
  drivenet.py onenet.py  조향·페달 신경망 / 판단까지 하는 하나의 신경망
  field.py fieldproc.py gpfield.py racecontrol.py   20대 필드(별도 프로세스), 레이스 컨트롤
  app.py hud.py post.py shaders.py lighting.py car.py   게임 본체, 화면, 렌더
  recorder.py          F9 녹화
tools/                 학습·평가·미리보기·검증 (위 표)
assets/                모델, 계획(racelines), 고스트, 정책(policies)
policy/                학습 결과 (policy/raceai, policy/drivenet)
```

## 한계

- 트랙은 평면이다(f1tenth 데이터에 고도가 없다).
- 타이어 온도·마모·연료·피트스톱이 없다.
- 서킷 이름·팀 이름은 가상이거나 실제 서킷명을 그대로 쓴다. 공개 배포 시 트랙 데이터(MIT)와 Kenney 에셋(CC0)의
  출처 표기는 저장소 루트 README를 따른다.

## 게임패드 (Switch 프로콘 등)

벤더 ID로 자동 인식해 게임패드 프로필을 쓴다(휠이 같이 연결돼 있으면 휠이 우선).

| 입력 | 동작 |
|---|---|
| 왼쪽 스틱 좌우 | 조향 (설정의 STEERING SENSITIVITY 적용) |
| ZR / ZL | 가속 / 브레이크 (버튼이라 눌렀을 때 부드럽게 올라감) |
| B | 핸드브레이크 (메뉴에서는 뒤로) |
| A | 확인 (레이스 중에는 뒤 보기) |
| X / Y | 카메라 전환 / DRS (레이스 밖에서는 타워 간격 전환) |
| L / R | 변속 다운 / 업 (MANUAL GEARBOX) |
| + | 일시정지 · 홈 = 설정(메뉴)  |
| 왼쪽/오른쪽 스틱 누르기 | 음소거 / HUD 숨김 |
| 십자키·왼쪽 스틱 | 메뉴 이동 (길게 누르면 반복) |
| − 누른 채 + Y / A / X / B / L / R | 리셋 / 관전 전환 / 프리캠 / 레이싱 라인 복귀 / 카드 숨김 / 뒤 보기 |
| − 만 탭 | 운전 보조 on/off |
