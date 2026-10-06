# FORMULA-AI

직접 운전하는 3D 레이싱 게임. 20대의 AI와 같은 그리드에서 출발하는 그랑프리와, 기록을 겨루는 퀄리파잉이 있다.
서킷은 실제 F1 서킷 23개(데이터: f1tenth_racetracks). 전체 방향은 [CONCEPT.md](CONCEPT.md).

## 실행

**`setup.bat`** 을 한 번 더블클릭하면 Python 환경(`.venv`)을 만들고 패키지를 설치한다(인터넷 필요).
그 다음부터 **`run.bat`** 을 더블클릭하면 게임이 열린다.

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
| F9 | 화면 녹화 시작·종료 (ffmpeg 필요, `내 동영상\FORMULA-AI`에 저장) |
| ESC | 일시정지 |

## 게임 모드

- **퀄리파잉**: 혼자 달려 기록을 낸다. 같은 난이도 AI 19명의 예선 기록이 타워에 나오고, 폴 드라이버의 랩이 고스트로 달린다.
- **그랑프리**: 플레이어 1 + AI 19(10팀 × 2)가 소등 출발한다. 접촉, 슬립스트림, DRS, 옐로 플래그(모든 차 100 km/h 이하),
  스튜어드 판정(트랙 리밋·접촉 페널티)이 모두 작동한다. 출발 순위는 서킷 선택 화면에서 A/D로 고른다.
- **난이도 1–6** (`game/teams.py`): 낮은 그립으로 푼 계획을 따라 달리게 해 단계를 만든다. 모나 폴 랩타임:
  Novice 99.8 s, Rookie 96.3, Amateur 94.1, Club 92.1, Pro 89.2, Legend 88.1.

## 설정 (메인 메뉴·서킷 선택에서 `O`)

세션을 시작하기 전에만 바꿀 수 있고(경기 중에는 불가), 값은 `~/.formula-ai/settings.json`에 저장된다.

| 항목 | 효과 |
|---|---|
| AUTO STEERING | 조향을 추종기가 맡는다. A / D를 누르고 있는 동안은 내 조향이 우선이고, 놓으면 추종기가 그 자리의 선을 이어서 따라간다(레이싱 라인으로 되돌아가지 않고, 흰 선 밖으로는 나가지 않게). Q는 레이싱 라인으로 복귀 |
| AUTO PEDALS | 가속·브레이크도 자동(W / S를 누르는 동안은 내 입력이 우선). 선택한 난이도의 중위권 AI와 같은 계획과 페이스로 달리고(Novice는 느리고 조심스럽게, Legend는 전력으로), 앞차 뒤에서는 간격을 유지. 조향까지 켜면 옆으로 움직이는 입력만으로 주행한다 |
| YELLOW FLAGS | 멈춘 차가 옐로를 내고 모두 감속. 끄면 옐로 자체가 없다 |
| DRS, SLIPSTREAM | 각각 끌 수 있다 |
| PENALTY · TRACK LIMITS / COLLISIONS / YELLOW FLAGS | 종류별로 시간 페널티(경고 포함)를 끈다 |

자동 조향·페달은 계획 해가 있는 서킷에서만 켜진다.

## AI는 어떻게 달리나

한 대의 AI는 네 층이다. 위로 갈수록 판단, 아래로 갈수록 손발.

1. **계획** — 서킷마다 차 물리 모델로 푼 최소 랩타임 최적해(`tools/mintime.py`, CasADi + IPOPT, 그립 0.97–0.74 일곱 단계).
   어디로 얼마나 빨리 달릴지의 기준선이다. 모나 해 88.08 s, 게임 물리로 주행하면 88.05 s.
2. **판단 층** (`game/racecraft.py`, `game/raceai.py`) — 0.13초마다 라인 오프셋과 페이스를 정한다. 추월, 수비, 합류, 줄서기.
   **학습된 정책**이 기본이다(`config.RACE_AI = "rl"`, 가중치 `assets/policies/raceai.npz`, numpy 추론).
   파일이 없거나 `"rules"`이면 손으로 짠 규칙이 결정한다.
3. **추종** (`game/mintime_driver.py`) — 오프셋·속도 제한을 받아 조향과 페달을 만든다. 손으로 만든 피드포워드 + 피드백 제어기.
   이 층을 신경망으로 바꾸는 작업이 진행 중이다(`game/drivenet.py`, `config.DRIVE_AI`, 아직 기본값 아님).
4. **안전·규칙** — 옆 차와의 간격, 앞차 간격 제한, 옐로, 이탈 시 복구, 레이스 컨트롤(스튜어드).

판단 층 학습 결과 (8개 서킷 × 8시드, 정책이 한 대만 운전): 장면 평균 성공률 규칙 0.71 → 학습 0.73(오차 범위),
20대 3랩 레이스에서 복구 22.8 → 14.5회, 뒤차 쪽 차선 이동 122 → 70회/랩, 추월 15.0 → 10.0회/랩.

**알려진 문제**: 출발 직후 첫 코너에서 20대 중 4~5대가 접촉 없이 코스를 벗어난다(추종 제어기가 큰 라인 오차에 감속하지 않음).
계획 라인 자체가 코너의 약 20%를 연석 위에서 달려 코너 커팅이 보이고, 앞차에 막혀 있는 시간의 절반가량은 옆이 비어 있다.
신경망 주행(`drivenet`)과 사람다움 보상(연석, 줄서기, 입력의 부드러움)으로 해결하는 중이다.

### 학습·평가 도구

| 명령 (저장소 루트에서) | 하는 일 |
|---|---|
| `tools/race_metrics.py [--policy X.npz] [--races N] [--all-cars] --name X` | 5개 장면과 전체 레이스 지표 (성공률, 접촉, 복구, 연석, 줄서기). 결과 `policy/raceai/X.json` |
| `tools/ppo_raceai.py --bc policy/raceai/bc.npz --out DIR --jobs 4` | 판단 층 PPO |
| `tools/dagger_drive.py train --out DIR --jobs 4` / `eval --net X.npz` | 조향·페달 신경망 DAgger 학습 / 교사와 비교 평가 |
| `tools/ppo_drive.py --init DIR/dagger_last.npz --out DIR2` | 신경망 주행 PPO(연석·이탈·부드러움 보상) |
| `tools/solve_all.py` | 모든 서킷의 계획 계산 (한 번에 한 서킷, 메모리 큼) |
| `tools/race_sim.py`, `tools/smoke_raceai.py`, `tools/smoke_headless.py` | 화면 없는 레이스 시뮬, 배관 점검, 물리 검증 |

Windows에서는 학습 작업자를 4개까지만 쓴다(그 이상은 메모리 오류). 학습 결과 파일(`policy/`)과 Kaggle 업로드 스크립트는 이 저장소에 없다.

## 구조

```
run.py, run.bat, setup.bat   게임 시작, 환경 설치
game/
  config.py            튜닝 상수 전부
  vehicle.py           동역학 자전거 모델 (Pacejka 타이어, 마찰원, 하중이동, TC/ABS/ESC)
  surface.py trackdata.py trackmesh.py scenery.py props.py terrain.py   트랙·노면·경치
  mintime_driver.py    계획과 추종 제어기
  assist.py settings.py   초보자 보조(자동 조향·페달·차선)와 세션 설정
  racecraft.py raceai.py raceenv.py   판단 층 (규칙 / 학습 정책) 과 학습용 환경·심판
  drivenet.py          조향·페달 신경망 (진행 중)
  field.py fieldproc.py gpfield.py racecontrol.py   20대 필드(별도 프로세스), 레이스 컨트롤
  app.py hud.py post.py shaders.py lighting.py car.py   게임 본체, 화면, 렌더
  recorder.py          F9 녹화
tools/                 학습·평가·미리보기·검증 (위 표)
assets/                모델, 계획(racelines), 고스트, 정책(policies)
data/                  서킷 데이터 (f1tenth_racetracks)
```

## 한계

- 트랙은 평면이다(f1tenth 데이터에 고도가 없다).
- 타이어 온도·마모·연료·피트스톱이 없다.
- 서킷 이름·팀 이름은 가상이거나 실제 서킷명을 그대로 쓴다. 트랙 데이터는 f1tenth_racetracks(MIT), 일부 도로변 모델은 Kenney Racing Kit(CC0)이다
  (`assets/models/kenney/LICENSE.txt`).
