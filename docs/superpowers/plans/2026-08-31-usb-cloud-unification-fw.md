# USB 본선 Cloud 형식 통일 — 펌웨어 구현 계획 (계획 1/2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** USB 본선(USART3)이 젯슨(J29)과 같은 Cloud 형식 NDJSON 을 말하게 하고, mk_cloud 를 유일 직렬화기로 만든다.

**Architecture:** mk_cloud 가 mk_telem 의 소비 골격(tx.period_ms 틱, ain 큐 드레인 라운드로빈, last 반복)을 상속하고, mk_telem 은 은퇴한다. main.c 의 emit 콜백 하나가 같은 줄을 두 링크(젯슨 항상, USB 는 HB 게이트)에 넣는다. `$` 제어/진단 줄은 통일 대상 밖.

**Tech Stack:** C(HAL 비의존 app 층), 호스트 실행 단위시험(`firmware/stage1/tests/`, gcc 네이티브), PowerShell 시험 러너.

**Spec:** `HANDOFF_0831.md` (저장소 루트) — 결정 1·2·3, 검토 1~8. 배경 설계는 `docs/superpowers/specs/2026-08-21-cloud-schema-jet-link-design.md` (§2 "본선 v3 유지"·§4.7 "송신 주기 없음" 두 항목은 이 계획으로 **폐기**된다).

## Global Constraints

- 🔴 **이 계획이 끝나도 보드에 굽지 않는다.** GUI 가 새 형식을 읽는 계획 2(호스트)까지 끝난 뒤, **사용자에게 알리고** 굽는다(메모리 규칙: 굽기 전에 알린다).
- app/ 층은 HAL 을 include 하지 않는다 (CLAUDE.md — 호스트 단위시험의 전제).
- 시험 실행: `powershell -File firmware/stage1/tests/run_tests.ps1` — 전부 통과 상태를 유지한다. 새 시험 파일은 러너와 check_sources 목록 양쪽에 등록한다(기존 test_cloud.c 등록부를 따라 한다).
- 호스트 파이썬 시험(`python -m pytest -q`)은 이 계획의 영향권 밖이지만, 각 태스크 커밋 전에 한 번 돌려 회귀가 없음을 확인한다(펌웨어만 고치므로 깨지면 안 된다).
- 커밋: Conventional Commits + 한국어 제목, 본문은 "왜". 트레일러:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 젯슨 수신 줄은 한 글자도 달라지면 안 되는 것이 원칙이나, **예외 2개가 사용자 확정**됐다: `seq` 필드 추가, valve 레코드의 발행원이 `cloud.valve` → `dinN.cloud` 로 이동(레코드 모양은 동일).
- 레코드의 `t` 는 항상 획득 시각. 송신 시각으로 덮지 않는다(설계 원칙 2).

---

### Task 1: mk_cloud 에 `seq` 필드 — `tx.seq` 체크박스로 켜고 끔

**Files:**
- Modify: `firmware/stage1/app/mk_cloud.h` (MkCloud 구조체, 39~65행)
- Modify: `firmware/stage1/app/mk_cloud.c` (`begin_record`, `finish_and_emit`)
- Modify: `firmware/stage1/app/mk_cfgtable.c` (tx 그룹, 360행 `tx.period_ms` 옆)
- Test: `firmware/stage1/tests/test_cloud.c`

**Interfaces:**
- Consumes: 기존 `begin_record(c, &j, out, cap, type, t_ms)` / `finish_and_emit(...)`
- Produces: 카탈로그 키 `tx.seq` (bool, **기본 켜짐**, group "tx", label "순번(seq)",
  note "끄면 GUI 도 젯슨도 링크 유실을 세지 못한다"). 켜져 있으면 모든 클라우드
  레코드에 `"seq":<u32>` (줄마다 1 증가, 재부팅 시 0 — 카운터는 끔 상태에서도
  증가해, 다시 켰을 때 번호가 이어진다). 이후 태스크의 시험은 기본값(켜짐)을
  전제한다 (사용자 결정 2026-08-31: 체크박스로).

- [ ] **Step 1: 실패하는 시험**

test_cloud.c 의 기존 직렬화 시험 함수들 옆에 추가 (하네스 `CHECK`/`CHECK_HAS`/`sink`/`find_line` 재사용):

```c
static void test_seq_increments_per_line(void)
{
    printf("seq 는 줄마다 1 증가\n");
    setup_ain_flow();               /* 기존 시험들이 쓰는 ain 준비 헬퍼 그대로 */
    N = 0;
    push_ain_sample(0, 1000, 850000);   /* 기존 헬퍼 — 표본 1 주입 */
    mk_cloud_tick(&C, 1000, sink, NULL);
    push_ain_sample(0, 1010, 850100);
    mk_cloud_tick(&C, 1010, sink, NULL);
    CHECK(N >= 2, "두 줄 이상 나왔다");
    CHECK_HAS(LINES[0], "\"seq\":0", "첫 줄 seq=0");
    CHECK_HAS(LINES[1], "\"seq\":1", "둘째 줄 seq=1");
}

static void test_seq_checkbox_off_omits_field(void)
{
    printf("tx.seq 끄면 seq 필드가 빠진다\n");
    setup_ain_flow();
    set_u32("tx.seq", 0u);
    N = 0;
    push_ain_sample(0, 2000, 850000);
    mk_cloud_tick(&C, 2000, sink, NULL);
    CHECK(N >= 1 && strstr(LINES[0], "\"seq\"") == NULL, "seq 없음");
}
```

(주: `setup_ain_flow`/`push_ain_sample` 이 기존 파일에 다른 이름이면 그 이름을 쓴다 — test_cloud.c 상단의 기존 ain 시험이 쓰는 준비 코드를 그대로 복제해도 된다.)

- [ ] **Step 2: 실패 확인** — `powershell -File firmware/stage1/tests/run_tests.ps1` → 새 시험 FAIL("seq" 미포함).
- [ ] **Step 3: 구현** — mk_cfgtable.c 에 `tx.seq` bool 항목(360행 패턴). `mk_cloud.h` MkCloud 에 `uint32_t seq;` 추가. `mk_cloud.c` `begin_record` 에서 `schema_ver` 바로 다음에 `tx.seq` 가 켜져 있으면 `mk_json_u32(&j, "seq", c->seq);`. `finish_and_emit` 이 emit 을 부른 **성공 경로에서** 켬/끔과 무관하게 `c->seq++`(끔 상태에서도 번호가 이어지게). `device_capability`·`valve` 등 begin_record 를 안 타는 조립이 있으면 같은 자리에 수동 추가(전 레코드 공통이어야 한다 — `grep mk_json_u32.*schema_ver mk_cloud.c` 로 전수 확인).
- [ ] **Step 4: 통과 확인** — 러너 전체 PASS (기존 시험 중 필드 순서를 엄격 비교하는 것이 있으면 기대 문자열에 seq 를 반영).
- [ ] **Step 5: Commit** — `feat(fw): 클라우드 레코드에 seq — 링크 유실을 양쪽 링크에서 셀 수 있게`

---

### Task 2: I2C 포트별 송신 주기 — 카탈로그 항목 + 반복 송신

**Files:**
- Modify: `firmware/stage1/app/mk_cfgtable.c` (849행 `i2cN.period_ms` 생성부 옆)
- Modify: `firmware/stage1/app/mk_cloud.h`, `mk_cloud.c` (`tick_i2c`, 344행)
- Test: `firmware/stage1/tests/test_cloud.c`

**Interfaces:**
- Consumes: `mk_i2c_last(c->i2c, port, k, &o)` (기존), `i2c_u32(cfg, port, ".tx_period_ms", 200u)` (기존 i2c_u32 헬퍼)
- Produces: 카탈로그 키 `i2c10.tx_period_ms` ~ `i2c15.tx_period_ms` (U16, min 10, max 60000, 기본 200, unit "ms", group "i2c", label "송신 주기", note "짧게 잡으면 젯슨 링크가 포화할 수 있다 — 전송 탭 사용량을 확인"). tick_i2c 는 포트별로 "새 표본이거나 tx_period 가 찼으면" 캐시 최신값을 발행.

- [ ] **Step 1: 실패하는 시험**

```c
static void test_i2c_repeats_at_tx_period(void)
{
    printf("i2c 는 tx_period 마다 캐시 최신값을 반복한다\n");
    setup_i2c_humid();              /* 기존 temp_air/humidity 시험의 준비부 */
    set_u32("i2c13.tx_period_ms", 100u);
    push_i2c_value(13, 0, 5000, 25.4f);   /* t=5000 표본 1개 주입 */
    N = 0;
    mk_cloud_tick(&C, 5000, sink, NULL);
    int first = N;
    CHECK(first >= 1, "새 표본은 즉시 나간다");
    mk_cloud_tick(&C, 5050, sink, NULL);          /* 주기 미달 */
    CHECK(N == first, "주기 전에는 반복하지 않는다");
    mk_cloud_tick(&C, 5100, sink, NULL);          /* 주기 도달 */
    CHECK(N > first, "주기가 차면 같은 값이 반복된다");
    CHECK_HAS(LINES[N-1], "\"t\":5000", "반복 줄의 t 는 획득 시각 그대로");
}
```

- [ ] **Step 2: 실패 확인** — FAIL(주기 도달 시 반복 없음).
- [ ] **Step 3: 구현** — mk_cfgtable.c 849행 블록 뒤에 같은 `gen("i2c", jack, ".tx_period_ms", ...)` 패턴으로 항목 추가. mk_cloud.h MkCloud 에 `int64_t i2c_tx_last_ms[MK_I2C_COUNT];` 추가(슬롯이 아니라 포트 단위 — 온도·습도는 같은 수집에서 나오므로 함께 반복). tick_i2c 의 건너뛰기 조건을 다음으로 교체:

```c
uint32_t txp = i2c_u32(c->cfg, p, ".tx_period_ms", 200u);
int fresh = !(c->i2c_primed[p][k] && c->i2c_sent_t[p][k] == o.t_ms);
int due   = (now_ms - c->i2c_tx_last_ms[p]) >= (int64_t)txp;
if (!fresh && !due) { continue; }
```

발행 성공 시 `c->i2c_tx_last_ms[p] = now_ms;` (포트의 첫 슬롯 발행 시 1회). `tick_i2c` 시그니처에 `now_ms` 가 없으면 `mk_cloud_tick` 에서 내려준다.
- [ ] **Step 4: 통과 확인** + 기존 "같은 표본 두 번 안 낸다" 시험이 있으면 tx_period 기본 200ms 안에서는 여전히 참임을 확인.
- [ ] **Step 5: Commit** — `feat(fw): i2c 포트별 송신 주기 — 수집과 송신을 분리 (HANDOFF_0831 결정 1)`

---

### Task 3: I2C 수집 주기 하한 — 조용한 클램프 대신 사유 있는 거절

**Files:**
- Modify: `firmware/stage1/app/mk_i2c_drivers.c` (+ `mk_i2c.h` 선언)
- Modify: `firmware/stage1/app/mk_cfgwire.c` (SET 의 min/max 검증 분기 — `grep -n "min" mk_cfgwire.c` 로 위치 확인)
- Test: `firmware/stage1/tests/test_cfgwire.c`

**Interfaces:**
- Produces: `uint32_t mk_i2c_min_period_ms(uint32_t kind);` — 종류의 warmup 하한(AM2320 2000/MLX 250/BH1750 180, 없음·미지 종류는 0). SET `i2cN.period_ms` 가 현재 kind 의 하한 미만이면 거절, 사유 문자열에 하한 값 포함. kind 를 바꾸는 SET 에서 현재 period 가 새 하한 미만이면 period 를 하한으로 올리고 정상 처리(값을 지어내는 게 아니라 하한으로 정렬 — 사유 아님, 다음 LIST 에서 보임).

- [ ] **Step 1: 실패하는 시험** (test_cfgwire.c 의 기존 SET 거절 시험 패턴을 그대로 따른다 — 기존 min 위반 시험을 복제해 키와 기대 사유만 바꾼다):

```c
static void test_i2c_period_below_kind_floor_rejected(void)
{
    printf("종류 하한 미만 period_ms SET 은 사유와 함께 거절\n");
    /* i2c13.kind=2(온습도) 로 두고 */
    do_set("i2c13.kind", "2");
    const char *resp = do_set("i2c13.period_ms", "500");
    CHECK_HAS(resp, "ERR", "거절된다");
    CHECK_HAS(resp, "2000", "사유에 하한 2000 이 보인다");
    resp = do_set("i2c13.period_ms", "2000");
    CHECK_HAS(resp, "OK", "하한 이상은 통과");
}
```

- [ ] **Step 2: 실패 확인** — 현재는 조용히 수용되므로 FAIL.
- [ ] **Step 3: 구현** — mk_i2c_drivers.c 에 종류→드라이버 표(기존)에서 warmup_ms 를 돌려주는 함수 추가. mk_cfgwire.c 의 SET 검증에서 키가 `i2c*.period_ms` 형태이면 같은 포트의 `.kind` 를 찾아 하한 비교, 미만이면 기존 거절 경로로 사유(`"종류 하한 %ums"`)를 실어 반환. `i2c*.kind` SET 성공 직후 같은 포트 period 를 하한으로 상향. 🔴 mk_i2c.c 의 `retry_period_ms`(실효 max 클램프)는 **지운다** — 검증이 입구를 막으므로 이중 방어가 아니라 은폐가 된다(사용자 합의: 설정값 = 실제 동작). 단 FAULT 재시도 주기 용도(138행 주석)로 남는 부분은 유지.
- [ ] **Step 4: 통과 확인** — cfgwire 시험 전체 + i2c 시험 전체 PASS.
- [ ] **Step 5: Commit** — `fix(fw): i2c 수집 주기 하한을 조용히 덮지 않고 SET 에서 사유와 함께 거절`

---

### Task 4: ain 큐 드레인을 mk_cloud 로 이식

**Files:**
- Modify: `firmware/stage1/app/mk_cloud.h` (`last_ms`, `ain_rr` 추가), `mk_cloud.c` (`tick_ain`, 234행)
- Reference (이식 원본): `firmware/stage1/app/mk_telem.c:603-716` — 주기 대기·라운드로빈·기아 방지·last 반복의 검증된 원형
- Test: `firmware/stage1/tests/test_cloud.c`

**Interfaces:**
- Consumes: `mk_ads_channel_enabled(ads, ch)` / `mk_ads_queue(ads, ch)` / `mk_queue_pop(q, &s)` / `mk_ads_last(ads, ch, &s)` (전부 mk_telem 이 쓰던 그대로), `cfg_u32(cfg, "tx.period_ms", 100u)`
- Produces: `mk_cloud_tick` 은 ain 에 대해 ① `tx.period_ms` 를 기다렸다가 ② 채널 큐를 라운드로빈으로 드레인(바퀴당 채널마다 1표본, 예산 `MK_CLOUD_MAX_LINES` 신설 = mk_telem 의 MK_TELEM_MAX_LINES 와 같은 값) ③ 이번 틱 내내 큐가 빈 채널만 last 반복. gnss/imu/valve/capability/i2c 틱은 주기 대기 **밖**(지금처럼 매 tick).

- [ ] **Step 1: 실패하는 시험**

```c
static void test_ain_drains_queue_not_just_last(void)
{
    printf("ain 은 큐의 표본을 전부 내보낸다 (덮어쓰기 유실 없음)\n");
    setup_ain_flow();
    set_u32("tx.period_ms", 10u);
    /* 틱 없이 표본 3개를 큐에 쌓는다 — 슈퍼루프가 한눈판 상황 재현 */
    push_ain_sample(0, 1000, 850000);
    push_ain_sample(0, 1010, 850100);
    push_ain_sample(0, 1020, 850200);
    N = 0;
    mk_cloud_tick(&C, 1030, sink, NULL);
    int flow_lines = 0;
    for (int k = 0; k < N; k++)
        if (strstr(LINES[k], "\"type\":\"flow")) flow_lines++;
    CHECK(flow_lines == 3, "표본 3개 = 줄 3개 (last 만 읽으면 1개)");
    CHECK(find_line_with(“\"t\":1000”) && find_line_with("\"t\":1010"),
          "밀렸던 표본도 자기 획득 시각을 달고 나온다");
}

static void test_ain_round_robin_no_starvation(void)
{
    printf("한 채널이 밀려도 다른 채널이 굶지 않는다 (688ce00 회귀 방지)\n");
    setup_two_ain_channels();       /* ch0·ch1 켬 */
    for (int i = 0; i < 40; i++) push_ain_sample(0, 2000 + i, 850000 + i);
    push_ain_sample(1, 2000, 900000);
    N = 0;
    set_u32("tx.period_ms", 10u);
    mk_cloud_tick(&C, 2100, sink, NULL);
    CHECK(find_line_for_channel(1) != NULL,
          "ch1 의 표본이 같은 틱 안에 나왔다");
}
```

(`find_line_with`/`find_line_for_channel`/`setup_two_ain_channels` 는 이 태스크에서 하네스에 추가 — find_line 과 같은 strstr 순회.)

- [ ] **Step 2: 실패 확인** — 현재 tick_ain 은 last 만 읽으므로 flow_lines==1 로 FAIL.
- [ ] **Step 3: 구현** — mk_telem.c:677-716 의 드레인 루프를 tick_ain 으로 옮겨 심되, 줄 조립은 기존 `build_ain` 을 그대로 부른다. 주기 대기(603-618행 원형)는 `mk_cloud_tick` 진입부에서 ain 경로에만 건다. 기존 `ain_sent_t/ain_primed` dedupe 는 last-반복 경로에만 남긴다(큐 표본은 pop 자체가 소비라 dedupe 불요).
- [ ] **Step 4: 통과 확인** — 신규 2 + 기존 전체 PASS.
- [ ] **Step 5: Commit** — `feat(fw): mk_cloud 가 ain 큐를 상속 — 세어지지 않는 덮어쓰기 유실 봉쇄 (검토 1)`

---

### Task 5: din 타입 문자열 — `dinN.cloud` 와 valve 발행원 이동

**Files:**
- Modify: `firmware/stage1/app/mk_cfgtable.c` (din 항목 생성부 — `grep -n '"din"' mk_cfgtable.c`)
- Modify: `firmware/stage1/app/mk_cloud.c` (valve 레코드 틱 — `grep -n valve mk_cloud.c`)
- Modify: `tools/restore_board_config.py` (PLAN 에 `("din20.cloud", "valve", "젯슨 valve 레코드 발행원")` 추가)
- Test: `firmware/stage1/tests/test_cloud.c`

**Interfaces:**
- Consumes: `MkSolCtl.confirmed_state[MK_SOL_COUNT]` (mk_solctl.h:99), 기존 valve 틱의 상태 변화 감지 패턴
- Produces: 카탈로그 키 `din18.cloud`/`din19.cloud`/`din20.cloud` (자유 문자열, 기본 "", group 은 기존 din 항목과 동일 "sol", label "타입" — `ainN.cloud` 와 같은 결). 확정 상태가 바뀔 때마다 `{"schema_ver":1,"seq":N,"device_id":"1","t":<확정시각>,"type":"<문자열>","time_source":...,"state":0|1}` 1줄. 빈 문자열 = 미발행. **기존 valve 전용 틱은 제거** — `cloud.valve`(OR 지정)는 gnss 등 태깅 소스로만 남는다.

- [ ] **Step 1: 실패하는 시험**

```c
static void test_din_cloud_string_emits_state_record(void)
{
    printf("dinN.cloud 문자열이 상태 변화 레코드의 type 이 된다\n");
    setup_sol();                     /* 기존 valve 시험의 준비부 */
    set_str("din20.cloud", "valve"); /* set_u32 옆에 str 헬퍼 추가 */
    confirm_din(20, 1, 7000);        /* J20 확정 ON, t=7000 */
    N = 0;
    mk_cloud_tick(&C, 7001, sink, NULL);
    const char *l = find_line("valve");
    CHECK(l != NULL, "valve 레코드가 나온다");
    CHECK_HAS(l, "\"state\":1", "state=1");
    CHECK_HAS(l, "\"t\":7000", "t 는 확정 시각");
    confirm_din(20, 1, 7100);        /* 같은 상태 반복 — 변화 아님 */
    int before = N;
    mk_cloud_tick(&C, 7101, sink, NULL);
    CHECK(N == before, "변화 없으면 침묵");
}

static void test_din_cloud_empty_is_silent(void)
{
    printf("빈 문자열 = 미발행\n");
    setup_sol();
    set_str("din20.cloud", "");
    confirm_din(20, 1, 8000);
    N = 0;
    mk_cloud_tick(&C, 8001, sink, NULL);
    CHECK(find_line("valve") == NULL, "한 줄도 안 나간다");
}
```

- [ ] **Step 2: 실패 확인** — `din20.cloud` 키가 없어 FAIL.
- [ ] **Step 3: 구현** — 카탈로그 항목 3개 추가(ainN.cloud 의 STR 항목 정의를 복제, 커밋 5e1c719 참고). mk_cloud 에 `uint8_t din_sent_state[MK_SOL_COUNT]; uint8_t din_primed[MK_SOL_COUNT];` 추가, 기존 valve 틱을 일반화한 `tick_din` 으로 교체(채널 순회, cloud 문자열 비면 continue). 기존 valve 시험이 `cloud.valve` 지정 기반이면 `din20.cloud` 기반으로 고쳐 두고, gnss valve **태깅** 시험은 그대로 두어 태깅이 `cloud.valve` 를 계속 따름을 지킨다.
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: Commit** — `feat(fw): 디지털 입력도 사용자 문자열 타입으로 발행 — valve 는 din20.cloud 로 이동 (검토 5)`

---

### Task 6: gnss.echo 를 `$GNSSRAW` 진단 줄로

**Files:**
- Modify: `firmware/stage1/main.c` (슈퍼루프 — 830행 부근)
- Reference (이식 원본): `firmware/stage1/app/mk_telem.c` 의 gnss_raw 드레인(512행 주석 "gnss 원시 큐는 자체" 부근 — 큐 API 를 그대로 쓴다)
- Test: `firmware/stage1/tests/test_hostlink.c` 또는 신규 시험 없이 Task 8 의 회귀로 커버해도 되나, 여기서는 main.c 로직을 app 층 헬퍼로 빼서 시험한다: Create `firmware/stage1/app/mk_gnssecho.c`/`.h`, Test `firmware/stage1/tests/test_gnssecho.c`

**Interfaces:**
- Produces: `int mk_gnssecho_tick(MkGnss *g, MkConfig *cfg, MkCloudEmit emit, void *ctx);` — `gnss.echo` 켜짐일 때 원시 큐의 각 줄을 `"$GNSSRAW,"` + 원문(`$` 포함, CR/LF 제외) + `"\n"` 으로 emit. 원문의 NMEA 체크섬이 그대로 실리므로 추가 체크섬 없음. main.c 는 이것을 **USB emit 에만** 배선한다(Task 7) — 젯슨으로는 안 나간다.

- [ ] **Step 1: 실패하는 시험** (러너·check_sources 에 test_gnssecho 등록 포함)

```c
static void test_echo_wraps_raw_line(void)
{
    printf("$GNSSRAW 는 원문을 그대로 감싼다\n");
    /* mk_gnss 에 원시 줄 주입 — test_telem 의 기존 gnss_raw 시험 준비부 재사용 */
    feed_gnss_raw("$GNGGA,000000.00,,,,,0,00,99.99,,,,,,*66");
    set_u32("gnss.echo", 1u);
    N = 0;
    mk_gnssecho_tick(&G, &CFG, sink, NULL);
    CHECK(N == 1, "한 줄");
    CHECK(strncmp(LINES[0], "$GNSSRAW,$GNGGA,", 16) == 0, "머리말+원문");
    set_u32("gnss.echo", 0u);
    feed_gnss_raw("$GNGGA,000001.00,,,,,0,00,99.99,,,,,,*67");
    N = 0;
    mk_gnssecho_tick(&G, &CFG, sink, NULL);
    CHECK(N == 0, "꺼져 있으면 침묵 (큐는 소비)");
}
```

- [ ] **Step 2: 실패 확인** — 모듈 부재로 컴파일 실패 = FAIL.
- [ ] **Step 3: 구현** — mk_telem 의 gnss_raw 드레인 로직을 새 모듈로 이식하되 JSON 조립 대신 `$GNSSRAW,` 접두 문자열 조립. 꺼져 있어도 큐는 비운다(mk_telem 의 기존 규칙 유지 — 안 비우면 넘친다).
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: Commit** — `feat(fw): gnss 원문 에코를 $GNSSRAW 진단 줄로 — 레코드 통일 대상 밖으로 (검토 6)`

---

### Task 7: main.c 재배선 — 두 링크 한 직렬화기, 링크별 게이트

**Files:**
- Modify: `firmware/stage1/main.c` (128-141행 emit 콜백들, 597-609행 init, 828-833행 틱)
- Test: 이 태스크는 배선이라 호스트 실행 시험이 없다 — 컴파일(`make` 가능 환경이면 빌드, 아니면 `run_tests.ps1` 의 check_sources 로 참조 무결성) + Task 8 의 시험 삭제·전환으로 커버.

**Interfaces:**
- Consumes: Task 1~6 의 mk_cloud/mk_gnssecho. `mk_hostlink_mode(&link, now) == MK_MODE_CONFIG` (기존 HB 신선도 판정, 828행).
- Produces: main.c 에

```c
static int s_host_alive = 0;

/* 통일 직렬화기의 출구 — 같은 줄이 두 링크로. 젯슨 항상, USB 는 HB 게이트. */
static void emit_records(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_jet_write(line, len);
    if (s_host_alive) { mk_uart_write_bulk(line, len); }
}

/* $GNSSRAW — USB 전용 진단. 젯슨은 $ 줄을 버리지만 대역을 아예 안 쓴다. */
static void emit_echo_usb(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    if (s_host_alive) { mk_uart_write_bulk(line, len); }
}
```

슈퍼루프(828-833행)를 다음으로 교체:

```c
s_host_alive = (mk_hostlink_mode(&link, now) == MK_MODE_CONFIG);
mk_cloud_tick(&s_cloud, now, emit_records, NULL);
mk_gnssecho_tick(&s_gnss, &s_cfg, emit_echo_usb, NULL);
```

`mk_telem_init/attach/tick/set_host_alive` 호출과 `emit_telem`/`emit_cloud` 콜백 제거. mk_cloud 에 `mk_cloud_attach_gnss(&s_cloud, &s_gnss)` `mk_cloud_attach_timeax(...)` 등 mk_telem 만 물고 있던 수집원이 있으면 마저 배선(605-609행과 그 위 telem attach 목록 대조).

- [ ] **Step 1: 위 배선 구현** (수정 자체가 스텝 — 시험은 컴파일/참조 무결성)
- [ ] **Step 2: `run_tests.ps1` 전체 PASS + `python -m pytest -q` 회귀 없음 확인**
- [ ] **Step 3: Commit** — `feat(fw): 본선을 mk_cloud 로 전환 — 한 직렬화기, 링크별 게이트 (HANDOFF_0831 결정 2·검토 2)`

---

### Task 8: mk_telem 은퇴 — 🔴 [실행 중 조정 2026-08-31] 파일 삭제는 계획 2로 이관

**이유 (실행 중 발견)**: 호스트 시험이 mk_telem **파일 자체**를 읽는다 —
`host/tests/test_field_budget.py:353` 이 mk_telem.c 의 body 버퍼 크기를,
`host/tests/test_firmware_uart_dma.py:238` 이 mk_telem.h 의
MK_TELEM_MAX_LINES 를 대조한다. 지금 지우면 계획 1 의 전제(호스트 시험
초록 유지)가 깨지고, 이 host 시험들의 재작성은 어차피 계획 2(link_usage·
한도 재계산)의 일부다. Task 7 로 mk_telem 은 **배선에서 이미 빠졌다**
(죽은 코드 — main.c 가 부르지 않음). 삭제·mk_telem_raw_to_ma 의
mk_ads1256 이사·해당 host 시험 갱신은 계획 2 에서 한 몸으로 한다.

**Files:**
- Delete: `firmware/stage1/app/mk_telem.c`, `firmware/stage1/app/mk_telem.h`
- Modify: `firmware/stage1/Makefile` (SRCS 목록), `firmware/stage1/tests/run_tests.ps1`(러너·check_sources 목록)
- Delete/Modify: `firmware/stage1/tests/test_telem.c` — 삭제하되, 그 안에서 **동작 계약을 검증하던 시험**(마스크 동작, last 반복, gnss_raw, din)은 Task 2~6 에서 mk_cloud/mk_gnssecho 쪽 시험으로 이미 대체됐는지 하나씩 대조하고, 대체 안 된 계약이 있으면 test_cloud.c 에 옮겨 심은 뒤 삭제한다.

- [ ] **Step 1: test_telem.c 의 시험 목록을 뽑아 대체 여부 표로 만든다** (파일 안 함수명 grep → Task 2~6 시험과 짝짓기; 짝 없는 것은 test_cloud.c 로 이식)
- [ ] **Step 2: 파일 삭제 + 빌드 목록 정리**
- [ ] **Step 3: `run_tests.ps1` 전체 PASS 확인** (check_sources 가 삭제 파일을 더 안 찾는지 포함)
- [ ] **Step 4: Commit** — `refactor(fw): mk_telem 은퇴 — 규격 v3 본선 직렬화의 종료`

---

### Task 9: 규격·설계 문서 개정

**Files:**
- Modify: `protocol/specification.md` — §7 텔레메트리 절: 본선 레코드가 Cloud 계약 형식임을 명시(공통 필드에 `seq` 포함), v3 레코드 표(§7.2 ain·§7.5 i2c·§7.6 din·§7.7 gnss_raw·§7.8 gnss)를 새 형식 기준으로 교체, §7.7 은 `$GNSSRAW` 진단 줄 절로 이동, 침묵 게이트(§7.1.3)는 "USB emit 게이트" 로 서술 갱신. 개정 이력 표 갱신.
- Modify: `docs/superpowers/specs/2026-08-21-cloud-schema-jet-link-design.md` — §2 "본선은 v3 그대로"·§4.7 "송신 주기 두지 않는다" 에 폐기 표시(취소선 + "2026-08-30 사용자 결정으로 폐기, HANDOFF_0831 결정 1·2" 링크). 🔴 docs/ 는 git 밖 — 사용자에게 변경 사실을 보고한다.
- Modify: 계약 문서 `docs/데이터 스키마 명세서_v1.7*.md` — `seq` 를 선택 공통 필드로, din 유래 사용자 타입 문자열을 §15 개정 절차로 명시. 파일이 이 저장소에 없으면(상위/전작 소유) 사용자에게 반영 요청으로 보고만 한다.

- [ ] **Step 1: specification.md 개정** (v3 → 통일 형식; 예시 JSON 은 Task 1~5 시험이 검증한 실제 줄을 복사)
- [ ] **Step 2: 설계 문서 폐기 표시 + HANDOFF.md 의 "지금 상태" 표에 이 전환 1행 추가**
- [ ] **Step 3: Commit** — `docs(proto): 본선 텔레메트리를 Cloud 형식으로 개정 — v3 레코드 절 은퇴` (docs/ 밖 파일만 커밋됨을 확인)

---

## 계획 2 (호스트) 예고 — 이 계획 완료 후 별도 작성

records.py 파서 재작성, 카탈로그 역매핑+중복 경고, link_usage 재계산(두 링크), 보드 파라미터 파일 덤프(결정 3)와 restore_board_config 의 파일 읽기 전환, fake_board·879 시험 갱신. **fake_board 가 흉내 낼 "정답 출력"이 이 계획의 산출물이므로 순서가 뒤다.** 굽기는 계획 2 완료 후, 사용자 통지와 함께.

## Self-Review 결과

- HANDOFF_0831 대조: 결정 1(Task 2)·결정 2(Task 1·4·5·7·8)·검토 1(Task 4)·2(Task 7)·5(Task 5)·6(Task 6)·7(Task 9)·8(Task 2·3) — 결정 3·검토 4 의 GUI 측·계획 2 범위는 예고 절에 명시. 누락 없음.
- 시험 헬퍼(setup_*/push_*/confirm_din/set_str/feed_gnss_raw)는 test_cloud.c·test_telem.c 의 기존 준비 코드를 지칭 — 실행자는 해당 파일의 실제 이름에 맞춘다(각 태스크에 명시).
- 타입 일관성: MkCloudEmit 시그니처·i2c_tx_last_ms[MK_I2C_COUNT]·mk_i2c_min_period_ms(kind)·emit_records/emit_echo_usb 이름이 태스크 간 일치함 확인.
