# Cloud 스키마 젯슨 링크 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** J29(USART2) 링크로 전작 Cloud 스키마(v1.7.0) NDJSON 을 내보내
기존 젯슨 앱이 무수정으로 Cloud 에 publish 하게 한다.

**Architecture:** 신규 `app/mk_cloud.c`(HAL 비의존)가 mk_telem 과 같은
수집원(MkAds·MkI2c·MkSolCtl·MkGnss)을 읽는 병렬 소비자로 서고, 그 출력이
기존 `bsp/mk_jet.c`(링+DMA)를 타고 나간다. 본선(USART3, 규격 v3)은 불변.

**Tech Stack:** C(펌웨어 app 층, MSVC/호스트에서 시험), mk_json, mk_cfgtable.

**Spec:** `docs/superpowers/specs/2026-08-21-cloud-schema-jet-link-design.md`
(계약 원문: `docs/데이터 스키마 명세서_v1.6.0.md` → Task 8 에서 v1.7.0)

## Global Constraints

- 🔴 커밋은 하지 않는다 — 이 저장소는 사용자가 요청할 때만 커밋한다
  (CLAUDE.md §8). 계획의 각 태스크는 시험 통과로 끝난다.
- 🔴 `app/` 은 HAL·stdio 를 include 하지 않는다 (test_firmware_safety.py 가
  강제). 문자열 조립은 `mk_json` 만.
- 🔴 mk_telem·본선 출력은 한 바이트도 바뀌면 안 된다 — 기존 pytest 887개가
  감시자다. 매 태스크 끝에 `python -m pytest -q` 로 확인한다.
- 🔴 C 시험 실행: `powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1`
  (개별 묶음은 tests/ 에서 `cl` 빌드 — run_tests.ps1 의 $suites 참고).
- 새 모듈은 **Makefile 의 C_SRC 와 run_tests.ps1 의 $suites 양쪽**에 등록한다
  — `tests/check_sources.py` 가 검사한다.
- 공통 필드 순서(모든 클라우드 레코드): `schema_ver, device_id, t, type,
  time_source, <페이로드...>[, valve]` — 계약 예시와 같은 순서로 고정한다.
- 열거 choice_labels 는 한국어, 전선 문자열은 mk_cloud 내부 표가 정한다.

---

### Task 1: mk_cloud 공통 직렬화 + ain 레코드 + 설정 항목

**Files:**
- Create: `firmware/stage1/app/mk_cloud.h`, `firmware/stage1/app/mk_cloud.c`
- Create: `firmware/stage1/tests/test_cloud.c`
- Modify: `firmware/stage1/app/mk_cfgtable.c` (ain{n}.cloud · ain{n}.valve_tag 추가)
- Modify: `firmware/stage1/Makefile`, `firmware/stage1/tests/Makefile`,
  `firmware/stage1/tests/run_tests.ps1` (mk_cloud·test_cloud 등록)

**Interfaces:**
- Consumes: `mk_ads_last(const MkAds*, int ch, MkSample*)`,
  `mk_cfg_find(MkConfig*, const char*)`, `mk_json_*`(mk_json.h),
  `mk_timeax_grade(const MkTimeAx*)`(붙어 있을 때만)
- Produces (뒤 태스크가 쓴다):
  ```c
  typedef void (*MkCloudEmit)(void *ctx, const char *line, size_t len);
  typedef struct MkCloud MkCloud;   /* mk_cloud.h 에 정의 */
  void mk_cloud_init(MkCloud *c, MkConfig *cfg, MkAds *ads,
                     const char *device_id);
  void mk_cloud_attach_timeax(MkCloud *c, MkTimeAx *timeax);
  int  mk_cloud_tick(MkCloud *c, int64_t now_ms, MkCloudEmit emit, void *ctx);
  ```
- ain{n}.cloud 열거값: 0=없음, 1=도료 분사압(pressure_paint/bar),
  2=유리알 분사압(pressure_bead/bar), 3=유량(flow/lpm) — 이 표는
  mk_cloud.c 의 `static const struct { const char *type, *field; } AIN_CLOUD[]`
  하나에만 둔다.

- [ ] **Step 1: 카탈로그 항목 추가** — `mk_cfgtable.c` 의 add_ain 루프
  (지금 `.name` 항목 뒤)에 두 항목. 기존 `gen()`·choice 패턴을 그대로 따른다
  (파일 안의 열거 항목 — `adc.drate` — 을 본보기로):

```c
        /* 🔴 클라우드 타입 — "이 채널의 센서가 클라우드에서 뭐라 불리는가"
         *    를 사용자가 정한다(설계 2026-08-21 §4.2). 센서를 딴 커넥터로
         *    옮기면 이 선택만 옮기면 된다 — 타입이름이 채널을 따라간다. */
        k = gen("ain", (unsigned)ch, ".cloud", jack, "클라우드");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_ENUM, .min = 0, .max = 3,
                                  .has_min = 1, .has_max = 1,
                                  .label = s_labels[k],
                                  .choices = "없음,도료 분사압,유리알 분사압,유량" };
        i++;
        k = gen("ain", (unsigned)ch, ".valve_tag", jack, "밸브 태깅");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_BOOL, .label = s_labels[k],
                                  .note = "레코드에 밸브 상태를 붙인다" };
        i++;
```

  ⚠️ choices 필드의 실제 멤버명은 mk_cfgtable.c 의 기존 열거 항목
  (`adc.drate`)에서 확인해 맞춘다. `MK_CFG_MAX_ITEMS`(mk_config.h)가
  항목 수 증가(+14)를 감당하는지 확인하고 모자라면 함께 올린다.

- [ ] **Step 2: 실패하는 C 시험** — `tests/test_cloud.c`. 기존
  `test_telem.c` 의 setup()/sink() 구조를 복사해 시작한다:

```c
static void test_ain_pressure_paint_record(void)
{
    setup();                              /* CFG·ADS 초기화, test_telem.c 패턴 */
    set_u32("ain0.cloud", 1u);            /* 도료 분사압 */
    set_last(0, 1000, 4026531);           /* 20 mA 근처 표본 */
    set_f32("ain0.zero", 4.0f);
    set_f32("ain0.scale", 9.375f);

    mk_cloud_tick(&C, 100, sink, NULL);

    CHECK_HAS(LINES[0], "\"schema_ver\":1", "계약 §15 — 1 고정");
    CHECK_HAS(LINES[0], "\"device_id\":\"1\"", "문자열 device_id");
    CHECK_HAS(LINES[0], "\"type\":\"pressure_paint\"", "선택한 타입으로");
    CHECK_HAS(LINES[0], "\"bar\":150.0", "(ma-zero)*scale, 소수 1자리");
    CHECK_HAS(LINES[0], "\"time_source\":\"device_clock\"", "timeax 없으면 device_clock");
    CHECK(strstr(LINES[0], "\"valve\":") == NULL, "태깅 안 켜면 valve 없음");
}

static void test_ain_none_is_not_published(void)
{
    setup();                              /* ain0.cloud 기본 = 없음 */
    set_last(0, 1000, 4026531);
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "없음 = 미발행 (계약 §16.6)");
}

static void test_moving_the_sensor_is_a_config_change(void)
{
    /* 🔴 이 설계의 존재 이유 — J3→J4 이동이 설정 한 칸인지. */
    setup();
    set_u32("ain0.cloud", 0u);
    set_u32("ain1.cloud", 1u);            /* J4 로 옮겼다 */
    set_last(1, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"type\":\"pressure_paint\"", "타입이 채널을 따라간다");
}
```

- [ ] **Step 3: 실행해 실패 확인** — test_cloud 를 run_tests.ps1 에 등록
  하고 돌려 컴파일 실패(mk_cloud 미존재)를 확인.

- [ ] **Step 4: mk_cloud 구현** — build_record(mk_telem.c)의 구조를
  따르되 마스크 없이 계약 §1 공통필드 + 타입별 페이로드:

```c
/* mk_cloud.c 핵심 — ain 채널 하나 직렬화 */
static const struct { const char *type; const char *field; } AIN_CLOUD[] = {
    { NULL, NULL },                       /* 0 = 없음 */
    { "pressure_paint", "bar" },
    { "pressure_bead",  "bar" },
    { "flow",           "lpm" },
};

static int build_ain(MkCloud *c, int ch, const MkSample *s,
                     char *out, size_t cap)
{
    uint32_t sel = ain_u32(c->cfg, ch, ".cloud", 0u);
    if (sel == 0u || sel >= sizeof AIN_CLOUD / sizeof *AIN_CLOUD) return 0;

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 1u);
    mk_json_str(&j, "device_id", c->device_id);
    mk_json_i64(&j, "t", cloud_epoch_ms(c, s->t_ms));
    mk_json_str(&j, "type", AIN_CLOUD[sel].type);
    mk_json_str(&j, "time_source", cloud_time_source(c));
    float ma = mk_telem_raw_to_ma(s->raw);   /* mk_telem.h 의 공용 환산 */
    mk_json_f32(&j, AIN_CLOUD[sel].field,
                (ma - ain_f32(c->cfg, ch, ".zero", 4.0f))
                  * ain_f32(c->cfg, ch, ".scale", 1.0f), 1);
    /* valve 태깅은 Task 3 에서 이 자리에 붙는다 */
    return mk_json_end(&j);
}
```

  time_source 매핑(설계 §4.1): `gnss_pps→"gnss"`, `gnss_nmea→"gnss_nmea"`,
  없거나 device_clock→"device_clock"`. mk_cloud_tick 은 채널마다
  `mk_ads_last()` 의 **t_ms 가 지난 tick 과 달라졌을 때만** 발행한다
  (새 표본 = 발행, 설계 §4.7) — 지난 t_ms 를 `MkCloud` 안 배열에 든다.
  줄 상한·개행은 emit_ain_sample(mk_telem.c) 계약과 동일: 넘치면 통째 버림.

- [ ] **Step 5: 시험 통과 확인** — run_tests.ps1 전체 + `python -m pytest -q`
  (887 passed 유지 — 카탈로그 항목이 늘었으니 crosscheck 는 Task 9 전까지
  스냅샷 불일치로 깨질 수 있다. 깨지면 그 항목만 Task 9 로 미루지 말고
  **catalog_snapshot.jsonl 에 새 항목 줄을 추가**해 맞춘다: 기존 cfg_item
  줄 형식을 복사, cfg_end count 갱신).

### Task 2: i2c 레코드 (temp_road·light·temp_air+humidity)

**Files:**
- Modify: `firmware/stage1/app/mk_cloud.c`(.h), `firmware/stage1/tests/test_cloud.c`
- Modify: `firmware/stage1/app/mk_cfgtable.c` (i2c1x.cloud · i2c1x.valve_tag)

**Interfaces:**
- Consumes: `mk_cloud_attach_i2c(MkCloud*, MkI2c*)` (신설),
  `MkI2c.last[port][slot]`(MkI2cOut — connector_id·quantity·value·have_value·
  t_ms), `MkI2c.last_valid[][]` — mk_telem.c 의 i2c 발행부(§682 근처)와
  같은 읽기 방식.
- Produces: quantity→클라우드 타입 표 —
  `"temp_object"→temp_road/degc`, `"lux"→light/lux`,
  `"temp"→temp_air/degc`, `"humidity"→humidity/pct`.
  표에 없는 quantity 는 미발행.

- [ ] **Step 1: 실패하는 시험** (test_telem.c 의 i2c 시험 셋업 복사):

```c
static void test_i2c_am2320_becomes_two_cloud_records(void)
{
    setup_with_i2c();                      /* I2C.last[0][0..1] 채우는 헬퍼 */
    set_u32("i2c13.cloud", 1u);            /* 발행 켬 */
    /* AM2320: slot0=temp 23.5, slot1=humidity 42.6 — test_telem.c 참고 */
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("temp_air"), "\"degc\":23.5", "온도 → temp_air");
    CHECK_HAS(find_line("humidity"), "\"pct\":42.6", "습도 → humidity");
}

static void test_i2c_default_is_not_published(void)
{
    setup_with_i2c();                      /* i2c13.cloud 기본 = 꺼짐 */
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "기본은 미발행");
}
```

- [ ] **Step 2: 실행해 실패 확인**
- [ ] **Step 3: 구현** — 카탈로그에 `i2c1x.cloud`(bool)·`i2c1x.valve_tag`
  (bool, 기본 꺼짐) 추가(add_i2c 루프, Task 1 Step 1 과 같은 요령).
  mk_cloud 에 quantity→타입 표(`I2C_CLOUD[]`)와 `build_i2c()` — build_ain
  과 같은 공통필드, 페이로드는 표의 field 로. 새 값 판정은 last 의 t_ms
  변화로(ain 과 동일).
- [ ] **Step 4: 시험 통과 + pytest**

### Task 3: valve 레코드 + cloud.valve + 채널 태깅

**Files:**
- Modify: `firmware/stage1/app/mk_cloud.c`(.h), `tests/test_cloud.c`
- Modify: `firmware/stage1/app/mk_cfgtable.c` (`cloud.valve` 열거 —
  새 그룹 "cloud": choices "없음,J18,J19,J20")

**Interfaces:**
- Consumes: `mk_cloud_attach_sol(MkCloud*, MkSolCtl*)` (신설),
  `MkSolCtl.confirmed_valid[ch]`·`confirmed_state[ch]` (mk_solctl.h)
- Produces: `cloud_valve_state(MkCloud*) -> int` (0/1 — gnss·태깅이 쓴다).
  cloud.valve 열거값 0=없음, 1=J18(ch0), 2=J19(ch1), 3=J20(ch2).

- [ ] **Step 1: 실패하는 시험**:

```c
static void test_valve_record_on_confirmed_edge(void)
{
    setup_with_sol();
    set_u32("cloud.valve", 3u);            /* J20 */
    sol_set_confirmed(2, 1);               /* J20 = 켜짐 (헬퍼) */
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"type\":\"valve\"", "");
    CHECK_HAS(LINES[0], "\"state\":1", "");
    N = 0;
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK(N == 0, "상태가 안 바뀌면 다시 안 나간다 — 엣지 기반");
}

static void test_valve_tag_checkbox_adds_the_field(void)
{
    setup_with_sol();
    set_u32("cloud.valve", 3u);
    sol_set_confirmed(2, 1);
    set_u32("ain0.cloud", 1u);
    set_u32("ain0.valve_tag", 1u);
    set_last(0, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("pressure_paint"), "\"valve\":1", "태깅 켠 채널만");
}
```

- [ ] **Step 2: 실행해 실패 확인**
- [ ] **Step 3: 구현** — `cloud_valve_state()`: cloud.valve 가 가리키는
  채널의 confirmed_state(무효·미지정이면 0). tick 에서 지난 상태와 다르면
  valve 레코드 1줄(공통필드 + `state`). build_ain/build_i2c 끝에
  `if (valve_tag) mk_json_u32(&j, "valve", cloud_valve_state(c));`.
- [ ] **Step 4: 시험 통과 + pytest**

### Task 4: gnss 레코드 (cs 보존 포함)

**Files:**
- Modify: `firmware/stage1/app/mk_gnss.c`(.h) — GGA 체크섬 2자리 보존
- Modify: `firmware/stage1/app/mk_cloud.c`(.h), `tests/test_cloud.c`,
  `tests/test_gnss.c`(cs 보존 시험 1개)

**Interfaces:**
- Consumes: MkGnss fix(lat_1e8·lon_1e8 int64, fix_quality, sats,
  have_hdop·hdop_1e2, epoch_ms) — mk_telem.c build_gnss_record 가 읽는
  그 구조를 그대로. `mk_cloud_attach_gnss(MkCloud*, MkGnss*)` 신설.
- Produces: MkGnss 에 `char gga_cs[3]`(대문자 2자리+NUL, 마지막 유효 GGA 의
  `*` 뒤 두 글자) — mk_gnss_feed 의 GGA 파싱 경로에서 채운다.

- [ ] **Step 1: 실패하는 시험** — test_gnss.c 에 cs 보존:

```c
    /* "$GNGGA,...*4F" 를 먹이면 gga_cs 가 "4F" */
```

  test_cloud.c 에 gnss 레코드:

```c
static void test_gnss_record_matches_the_contract(void)
{
    setup_with_gnss();                     /* 유효 fix 주입 (test_telem 참고) */
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"lat_e8\":", "1e-8 도 정수 그대로");
    CHECK_HAS(LINES[0], "\"hdop_x100\":", "이미 ×100 정수(hdop_1e2)");
    CHECK_HAS(LINES[0], "\"cs\":\"4F\"", "원 GGA 체크섬");
    CHECK_HAS(LINES[0], "\"valve\":0", "gnss 는 태깅 필수 (계약 §3)");
    N = 0;
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK(N == 0, "새 fix 없으면 안 나간다");
}
```

- [ ] **Step 2: 실행해 실패 확인**
- [ ] **Step 3: 구현** — mk_gnss.c GGA 경로에 cs 2자리 복사(체크섬 검증
  자리에 이미 두 글자가 있다 — 그 값을 저장만). mk_cloud 의 build_gnss:
  공통필드 + lat_e8/lon_e8/fix/sat/hdop_x100/cs/valve. 발행 판정은 fix 의
  epoch_ms(또는 갱신 카운터) 변화. `gnss.enabled` 꺼짐·fix 무효면 미발행.
  hdop 이 없으면(have_hdop=0) **레코드 자체를 미발행** — 계약 §3 이 필수로
  요구하므로 필드만 빼는 것은 계약 위반이다.
- [ ] **Step 4: 시험 통과 + pytest**

### Task 5: device_capability + 설정 변경 재발행

**Files:**
- Modify: `firmware/stage1/app/mk_cloud.c`(.h), `tests/test_cloud.c`

**Interfaces:**
- Consumes: Task 1~4 의 설정 키들, `FW_VERSION` — `mk_cloud_init` 에
  `const char *fw_version` 인자 추가(기존 호출부는 Task 7 에서 배선).
- Produces: 부팅 후 첫 tick 과, 관련 설정 변경이 감지된 tick 에
  `device_capability` 1줄.

- [ ] **Step 1: 실패하는 시험**:

```c
static void test_capability_is_first_and_reflects_config(void)
{
    setup();
    set_u32("ain0.cloud", 1u);
    set_u32("i2c13.cloud", 1u);            /* AM2320 → temp_air+humidity */
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"type\":\"device_capability\"", "첫 줄이 capability");
    CHECK_HAS(LINES[0], "\"pressure_paint\"", "");
    CHECK_HAS(LINES[0], "\"temp_air\"", "");
    CHECK_HAS(LINES[0], "\"humidity\"", "");
    CHECK_HAS(LINES[0], "\"fw_version\":\"0.1.0\"", "");
    CHECK(strstr(LINES[0], "\"valve\"") == NULL, "valve 는 안 넣는다 (계약 §16.3)");
}

static void test_capability_reemits_on_config_change(void)
{
    setup();
    mk_cloud_tick(&C, 100, sink, NULL);    /* 부팅 1회 */
    N = 0;
    set_u32("ain1.cloud", 3u);             /* 유량 추가 */
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK_HAS(LINES[0], "\"flow\"", "변경 즉시 재발행 (계약 §16.4)");
}
```

- [ ] **Step 2: 실행해 실패 확인**
- [ ] **Step 3: 구현** — sensors 배열은 mk_json 의 배열 API 로(없으면
  mk_json.h 에 `mk_json_array_str` 추가 — 본선이 안 쓰므로 출력 불변).
  변경 감지: 관련 키들의 값을 합친 지문(u32 XOR/합)을 tick 마다 비교.
- [ ] **Step 4: 시험 통과 + pytest**

### Task 6: IMU — #RAWIMUXA 파서 + imu 레코드

**Files:**
- Create: `firmware/stage1/app/mk_imu.h`, `firmware/stage1/app/mk_imu.c`
- Create: `firmware/stage1/tests/test_imu.c`
- Modify: `firmware/stage1/app/mk_gnss.c`(.h) — `#` 로 시작하는 줄을
  버리지 않고 콜백으로 넘기는 훅 (현재 조립기가 어떻게 줄을 가르는지
  mk_gnss.c 를 먼저 읽고, `$` 아닌 줄 처리 지점에 훅을 건다)
- Modify: `firmware/stage1/app/mk_gnssctl.c` — `gnss.imu` 켜짐이면
  `RAWIMUXA 0.1` 명령 송신 (기존 초기화 명령 대열에 추가)
- Modify: `firmware/stage1/app/mk_cfgtable.c` — `gnss.imu`(bool, 기본 꺼짐)
- Modify: `firmware/stage1/app/mk_cloud.c` — imu 레코드 발행

**Interfaces:**
- Consumes: `#RAWIMUXA,...;errflag,imutype,week,sec,status,zacc,-yacc,xacc,
  zgyro,-ygyro,xgyro*crc32` (UM981_Auto_Commands_R1.0.pdf p.20 §2.3.5)
- Produces:
  ```c
  typedef struct {
      int   valid;
      float ax, ay, az;       /* g   — 계수 2/32767 (Table 2-14, type 64) */
      float gx, gy, gz;       /* dps — 계수 250/32767 */
      int64_t t_ms;           /* 수신 시각(장치 ms) — 획득 시각 근사 */
      uint32_t seq;           /* 새 표본 판정용 증가 카운터 */
  } MkImuSample;
  int mk_imu_parse(const char *line, MkImuSample *out);   /* 1=성공 */
  ```
  🔴 축 재배열: 문서의 필드 순서는 Z, −Y, X — `out->ax = x`, `out->ay =
  -(-y필드값)`... 이 아니라 **ax=X필드, ay=−(수신한 −Y 필드), az=Z필드**
  로 복원한다 (자이로 동일). CRC32 는 A단계에서 검증하지 않고 형식만
  본다(필드 수·숫자 파싱 실패 시 무시) — 검증 추가는 후속.

- [ ] **Step 1: 실패하는 시험** — 문서 예시 문장으로:

```c
static void test_rawimux_example_from_the_manual(void)
{
    /* UM981_Auto_Commands_R1.0.pdf p.20 의 실제 예시 */
    MkImuSample s;
    int ok = mk_imu_parse(
        "#RAWIMUXA,COM1,0,60.0,FINE,2261,366772.050,0,0,0;"
        "00,64,2261,366772.050,0ac00000,16278,-70,172,-1044,-90,-200*bffb7522",
        &s);
    CHECK(ok == 1, "예시 문장이 파싱된다");
    /* zacc=16278 → az = 16278*2/32767 ≈ 0.9936 g (거의 1g — 정지 상태) */
    CHECK(s.az > 0.98f && s.az < 1.01f, "Z 가속도 ≈ 1 g");
    /* -y 필드 = -70 → ay = +70*2/32767 */
    CHECK(s.ay > 0.0f, "-Y 필드 부호를 복원한다");
}
```

- [ ] **Step 2: 실행해 실패 확인**
- [ ] **Step 3: mk_imu_parse 구현** — `;` 뒤 본문을 `,` 로 갈라 6개 수치
  (5·6번째 필드 뒤부터 zacc..xgyro), strtol 로. 계수 곱해 float.
- [ ] **Step 4: 배선** — mk_gnss 의 비-`$` 줄 훅 → main.c 에서
  mk_imu_parse → 최신 MkImuSample 을 mk_cloud 에 전달
  (`mk_cloud_attach_imu(MkCloud*, const MkImuSample*)` — 포인터만 들고
  tick 에서 seq 변화 시 발행). imu 레코드: 공통필드 + ax..gz (소수 3자리)
  — mx·my·mz 없음(v1.7.0 §7 완화). gnssctl: `gnss.imu` 켜짐 + 초기화
  단계에서 `RAWIMUXA 0.1` 전송 (기존 명령 문자열 대열의 관례로).
- [ ] **Step 5: 시험 통과 + pytest**

### Task 7: main.c 배선 — 미러 제거, mk_cloud 가동

**Files:**
- Modify: `firmware/stage1/main.c` — emit_telem 의 `mk_jet_write(line, len)`
  제거, mk_cloud 초기화·attach 일습, 슈퍼루프에 `mk_cloud_tick`
- Modify: `firmware/stage1/bsp/mk_jet.h` — 머리말 주석을 "미러" 에서
  "Cloud 스키마 링크" 로 (코드 불변)
- Modify: `host/tests/test_firmware_safety.py` — 미러를 전제한 서술이
  있으면 갱신 (test_jet_* 셋은 핀·RX 검사라 그대로 통과해야 정상)

**Interfaces:**
- Consumes: Task 1~6 의 mk_cloud API 전부.
- Produces: 젯슨 링크에 Cloud 스키마만 흐른다.

- [ ] **Step 1: main.c 배선**:

```c
static void emit_cloud(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_jet_write(line, len);
}
/* main() 에서 — mk_telem_init 근처: */
mk_cloud_init(&s_cloud, &s_cfg, &s_ads, DEVICE_ID, FW_VERSION);
mk_cloud_attach_i2c(&s_cloud, &s_i2c);
mk_cloud_attach_sol(&s_cloud, &s_sol);
mk_cloud_attach_gnss(&s_cloud, &s_gnss);
mk_cloud_attach_timeax(&s_cloud, &s_timeax);
mk_cloud_attach_imu(&s_cloud, &s_imu_sample);
/* 슈퍼루프 — mk_telem_tick 다음 줄: */
mk_cloud_tick(&s_cloud, now, emit_cloud, NULL);
```

  emit_telem 에서는 `mk_jet_write` 호출과 그 주석을 지운다.
- [ ] **Step 2: 전체 확인** — run_tests.ps1 + pytest + ARM 빌드
  (`make -C firmware/stage1`, HANDOFF §2 의 PATH). 셋 다 초록.

### Task 8: 계약 문서 v1.7.0 개정

**Files:**
- Modify→Rename: `docs/데이터 스키마 명세서_v1.6.0.md` →
  `docs/데이터 스키마 명세서_v1.7.0.md`

- [ ] **Step 1: 개정** — 설계 §7 의 다섯 항목 그대로: 변경 이력 v1.7.0 행
  (2026-08-21), 제목·헤더 버전, §2 type 집합에 temp_air·humidity, 신규
  §(temp_air·humidity 메시지 — §11 temp_road 를 본보기로 예시 JSON·필드
  표·단위), §12 표에 degc(대기)·pct, §16.3 sensors 허용값, §3 아래에
  "valve 는 모든 센서 레코드의 선택 필드" 문단, §7 IMU 의 mx·my·mz
  필수→선택 + 근거(UM981 RAWIMUX 자기계 없음, 문서 인용).
- [ ] **Step 2: 사용자 통지** — docs/ 는 git 밖이라 개정 이력 표가 유일
  기록임을 보고에 명시 (CLAUDE.md §0).

### Task 9: 호스트 — 그룹 이름표 + 카탈로그 스냅샷

**Files:**
- Modify: `host/gui/settings_form.py` (또는 그룹 이름표가 있는 곳 —
  `grep -rn "그룹" host/gui/settings_form.py` 로 찾는다) — `cloud` 그룹
  한국어 이름표("클라우드")
- Modify: `host/tests/catalog_snapshot.jsonl` — 새 항목들(cfg_item) 추가,
  cfg_end count 갱신 (Task 1 Step 5 에서 이미 했으면 검증만)
- Test: `python -m pytest -q` 887+ 전부

- [ ] **Step 1: 그룹 이름표 추가, pytest 초록 확인** — GUI 는 카탈로그를
  보고 새 항목을 저절로 그린다(§0 원칙). 스냅샷과 실물 카탈로그의 대조는
  실기기에서: `python -m tools.cli.markon_cli --port COM23 list`.

### Task 10: 굽기 + 실기기 검증 (젯슨 끝단까지)

- [ ] **Step 1: 사용자에게 알리고 굽기** — `cd firmware/stage1 &&
  python tools/flash_verified.py`. 🔴 굽기 전 NRST 선 분리 상태 확인
  (HANDOFF §7.4), 막히면 전원 20초.
- [ ] **Step 2: GUI 에서 설정** — J3 `클라우드`=도료 분사압(실물 유압 센서),
  i2c12(적외)·i2c13(온습도) `클라우드` 켬, `cloud.밸브`=J20, 저장.
- [ ] **Step 3: 젯슨 수신 검증** — 젯슨에서 (뷰어 스크립트는 지워졌으므로
  일회성으로):

```bash
python3 - <<'EOF'
import serial, json, time, collections
s = serial.Serial('/dev/ttyTHS1', 921600, timeout=0.2)
s.reset_input_buffer(); buf = b''; types = collections.Counter(); bad = 0
t0 = time.time()
while time.time() - t0 < 10:
    buf += s.read(8192)
    while b'\n' in buf:
        ln, buf = buf.split(b'\n', 1)
        try: types[json.loads(ln)["type"]] += 1
        except Exception: bad += 1
print(dict(types), "bad:", bad)
EOF
```

  기대: `device_capability` 1, `pressure_paint`·`temp_road`·`temp_air`·
  `humidity` 다수, bad 0. J20 에 신호를 주면 `valve` 1줄 + 태깅 변화.
- [ ] **Step 4: 계약 준수 확인** — 수신 줄 하나를 계약 문서의 해당 §와
  필드 단위로 대조 (필드명·타입·단위).
- [ ] **Step 5: HANDOFF 갱신** — §7.4 에 "J29 는 이제 Cloud 스키마" 절,
  규격서에 한 줄(설계 §2), 남은 것(B단계) 정리.

## Self-Review 결과

- 설계 §4.1~§4.9 ↔ Task 1~7 대응 확인. §7(계약 개정)=Task 8, §5 설정=각
  태스크에 분산, 시험 전략 §6=각 태스크 Step + Task 10.
- gnss hdop 없음 → 미발행 결정을 Task 4 에 명시 (계약 필수 필드라서).
- imu CRC32 미검증은 의도된 범위 축소로 Task 6 에 명시.
- 타입 시그니처 일관성: MkCloudEmit/attach 계열 이름 통일 확인.
