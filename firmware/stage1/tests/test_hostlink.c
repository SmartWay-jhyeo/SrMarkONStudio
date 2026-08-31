/* mk_hostlink 단위 시험. 보드도 크로스 툴체인도 필요 없다.
 *
 * 시각을 손으로 돌려가며 3초 타임아웃과 1 Hz 하트비트를 시험한다.
 * 실기기에서 3초를 기다릴 필요가 없다는 것이 HAL 비의존의 값이다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_hostlink.h"
#include "../app/mk_framing.h"
#include "../app/mk_ads1256.h"
#include "../app/mk_solctl.h"
#include "../app/mk_gnssctl.h"
#include "../app/mk_rtcm.h"
#include "../app/mk_timeax.h"
#include "../app/mk_linkbaud.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_EQ(got, want, msg) do {                                       \
    if (strcmp((got), (want)) != 0) {                                       \
        printf("  FAIL %s\n        got  %s\n        want %s\n",             \
               msg, (got), (want));                                         \
        failures++;                                                         \
    } else { printf("  ok   %s\n", msg); }                                  \
} while (0)

/* ---- 내보낸 줄을 모으는 통 -------------------------------------------- */

/* 🔴 줄 하나가 256 자를 넘을 수 있다. 7채널을 다 켠 $STAT 이 약 450 자다.
 *    처음에 256 으로 두었더니 뒷부분이 잘려 "마지막 채널이 안 실린다" 로
 *    보였다 — 펌웨어는 멀쩡했고 시험의 통이 작았던 것이다. 통을 레코드보다
 *    넉넉히 잡아, 잘림이 결함으로 오인되지 않게 한다.
 *    [2026-08-28] 최악 $STAT 이 1,920 까지 커져(rtcm_* 필드, mk_hostlink.c
 *    의 body 원장) 1024 → 2048 로 키웠다 — 같은 오인을 막는다. */
#define CAP_LINES 16
#define CAP_LINE_LEN 2048
typedef struct {
    char lines[CAP_LINES][CAP_LINE_LEN];
    int  n;
} Sink;

static void sink_emit(void *ctx, const char *line, size_t len)
{
    Sink *s = (Sink *)ctx;
    if (s->n >= CAP_LINES) {
        return;
    }
    size_t n = len < sizeof s->lines[0] - 1u ? len : sizeof s->lines[0] - 1u;
    memcpy(s->lines[s->n], line, n);
    s->lines[s->n][n] = '\0';
    s->n++;
}

static void sink_reset(Sink *s) { s->n = 0; }

static void setup(MkHostlink *h, Sink *s)
{
    sink_reset(s);
    mk_hostlink_init(h, sink_emit, s, "1", "0.1.0", "2.0");
}

/* 완성된 줄을 만들어 feed 한다. */
static void feed(MkHostlink *h, const char *payload, int64_t now_ms)
{
    char line[256];
    int n = mk_build_line(line, sizeof line, payload);
    mk_hostlink_feed(h, line, (size_t)n, now_ms);
}

/* 🔴 기대하는 줄의 체크섬을 손으로 적지 않는다. Task 1 에서 손으로 적은
 *    체크섬 때문에 시험이 길이 검사에 닿지도 못하고 헛돌았다. */
static const char *expect_line(const char *payload)
{
    static char buf[256];
    mk_build_line(buf, sizeof buf, payload);
    return buf;
}

/* ---- 모드 ------------------------------------------------------------- */

static void test_boots_in_run(void)
{
    /* 규격 §6.2 — 부팅 직후 기본값은 RUN 이다. USB 를 감지할 수 없으므로
     * 케이블이 아니라 하트비트로 판정한다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "부팅 직후는 RUN");
    CHECK(mk_hostlink_mode(&h, 999999) == MK_MODE_RUN, "HB 없이 오래 있어도 RUN");
}

static void test_hb_opens_config(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 5000);
    CHECK(mk_hostlink_mode(&h, 5000) == MK_MODE_CONFIG, "HB 받으면 CONFIG");
}

static void test_mode_boundary_is_strictly_greater(void)
{
    /* 🔴 `>` 이지 `>=` 가 아니다. 시뮬레이터(device_sim.mode)와 같은
     *    경계를 써야 한다 — 한쪽이 CONFIG 로 보는 순간에 다른 쪽이 RUN
     *    이면 설정 변경이 간헐적으로 거부되고 재현되지 않는다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 1000);
    CHECK(mk_hostlink_mode(&h, 1000 + 2999) == MK_MODE_CONFIG, "2999 ms 는 CONFIG");
    CHECK(mk_hostlink_mode(&h, 1000 + 3000) == MK_MODE_CONFIG, "정확히 3000 ms 도 CONFIG");
    CHECK(mk_hostlink_mode(&h, 1000 + 3001) == MK_MODE_RUN,    "3001 ms 부터 RUN");
}

static void test_hb_refreshes(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "HB", 3500);
    CHECK(mk_hostlink_mode(&h, 6000) == MK_MODE_CONFIG, "새 HB 가 시각을 민다");
    CHECK(mk_hostlink_mode(&h, 6501) == MK_MODE_RUN,    "그 뒤로 다시 3초");
}

/* ---- §6.3 회귀 시험 (규격이 필수로 지정) ------------------------------ */

static void test_broken_hb_does_not_open_config(void)
{
    /* 🔴 규격 §6.3 이 회귀 시험을 필수로 둔 지점이다.
     *
     *    기존 Q2 펌웨어(host_link.c:183-187)는 줄이 `$HB*` 로 시작하는지만
     *    보고 체크섬 검증 **전에** 시각을 갱신했다. Q2 에서 $HB 는 단순
     *    생존 신호라 무해했지만, 여기서 $HB 는 설정 변경을 여는 열쇠다.
     *    그대로 옮기면 잡음으로 깨진 프레임이 설정 변경을 계속 허용한다. */
    MkHostlink h; Sink s;

    setup(&h, &s);
    mk_hostlink_feed(&h, "$HB*FF\r\n", 8, 1000);
    CHECK(mk_hostlink_mode(&h, 1000) == MK_MODE_RUN, "체크섬 틀린 HB 는 CONFIG 를 열지 않는다");

    setup(&h, &s);
    mk_hostlink_feed(&h, "$HB*xx\r\n", 8, 1000);
    CHECK(mk_hostlink_mode(&h, 1000) == MK_MODE_RUN, "16진수 아닌 체크섬도 마찬가지");

    setup(&h, &s);
    mk_hostlink_feed(&h, "$HBQ*0A\r\n", 9, 1000);
    CHECK(mk_hostlink_mode(&h, 1000) == MK_MODE_RUN, "$HB 로 시작만 해서는 안 된다");

    /* 이미 CONFIG 인 상태에서 깨진 HB 가 와도 시각이 밀리지 않아야 한다. */
    setup(&h, &s);
    feed(&h, "HB", 1000);
    mk_hostlink_feed(&h, "$HB*FF\r\n", 8, 3500);
    CHECK(mk_hostlink_mode(&h, 4001) == MK_MODE_RUN, "깨진 HB 는 시각을 밀지 않는다");
}

static void test_broken_hb_sends_no_sack(void)
{
    /* 규격 §3·§6.1 — $HB 는 체크섬이 틀려도 SACK 를 보내지 않는다.
     * 1 Hz 로 오므로 링크가 나빠지면 초당 하나씩 쌓인다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    mk_hostlink_feed(&h, "$HB*FF\r\n", 8, 1000);
    CHECK(s.n == 0, "깨진 HB 에 아무것도 안 보낸다");
}

static void test_good_hb_sends_no_sack(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 1000);
    CHECK(s.n == 0, "정상 HB 에도 SACK 를 보내지 않는다");
}

/* ---- 명령 ------------------------------------------------------------- */

static void test_id_emits_record_then_sack(void)
{
    /* 규격 §5.2 — 데이터를 돌려주는 명령은 $SACK 앞에 NDJSON 을 먼저 보낸다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "ID", 1772200855875LL);
    CHECK(s.n == 2, "id 는 두 줄을 낸다");
    if (s.n == 2) {
        CHECK_EQ(s.lines[0],
                 "{\"schema_ver\":3,\"seq\":0,\"t\":1772200855875,\"type\":\"id\","
                 "\"device_id\":\"1\",\"fw\":\"0.1.0\",\"board_rev\":\"2.0\"}\n",
                 "첫 줄은 id 레코드");
        CHECK_EQ(s.lines[1], expect_line("SACK,ID,OK"), "둘째 줄은 SACK");
    }
}

static void test_id_does_not_claim_ok_when_record_does_not_fit(void)
{
    /* 🔴 레코드를 못 만들었는데 $SACK,ID,OK 를 보내면 호스트는 데이터를
     *    받았다고 믿는다. 그것이 무응답보다 나쁘다 — 무응답은 타임아웃으로
     *    드러나지만 거짓 OK 는 드러나지 않는다.
     *
     *    규격상 device_id 는 15자 이하이므로 지금은 닿지 않는 경로다.
     *    그래도 시험을 두는 이유는, 나중에 레코드에 필드를 더할 때 이
     *    계약이 조용히 깨지는 것을 막기 위해서다. */
    static char huge[300];
    MkHostlink h; Sink s;
    memset(huge, 'D', sizeof huge - 1);
    huge[sizeof huge - 1] = '\0';

    sink_reset(&s);
    mk_hostlink_init(&h, sink_emit, &s, huge, "0.1.0", "2.0");
    feed(&h, "ID", 1000);
    CHECK(s.n == 0, "레코드가 안 들어가면 SACK,ID,OK 도 보내지 않는다");
}

static void test_id_record_at_buffer_boundary(void)
{
    /* 줄바꿈과 NUL 자리까지 세는지 본다. 경계 바로 안쪽은 통과해야 한다.
     * device_id 길이를 늘려가며 마지막으로 통과하는 지점을 찾는다. */
    static char id[256];
    MkHostlink h; Sink s;
    int last_ok = -1;

    for (int len = 1; len < 240; len++) {
        memset(id, 'D', (size_t)len);
        id[len] = '\0';
        sink_reset(&s);
        mk_hostlink_init(&h, sink_emit, &s, id, "0.1.0", "2.0");
        feed(&h, "ID", 0);
        if (s.n == 2) {
            last_ok = len;
        } else {
            CHECK(s.n == 0, "안 되면 아무것도 안 보낸다 (반쪽 응답 금지)");
            break;
        }
    }
    CHECK(last_ok > 100, "규격상 device_id(15자)는 넉넉히 들어간다");
}

static void test_null_emit_does_not_crash(void)
{
    /* emit 이 없을 수 있다 — 아직 UART 가 준비되지 않은 부팅 초기 같은
     * 상황이다. 그때 NULL 을 부르면 보드가 죽는다. */
    MkHostlink h;
    mk_hostlink_init(&h, NULL, NULL, "1", "0.1.0", "2.0");
    feed(&h, "ID", 1000);
    feed(&h, "NOPE", 1000);
    mk_hostlink_feed(&h, "$ID*FF\r\n", 8, 1000);
    mk_hostlink_tick(&h, 1000);
    feed(&h, "HB", 1000);
    CHECK(mk_hostlink_mode(&h, 1000) == MK_MODE_CONFIG,
          "emit 이 NULL 이어도 죽지 않고 모드는 정상 동작한다");
}

static void test_id_works_in_run_mode(void)
{
    /* $ID 는 CONFIG 전용이 아니다 (§4). 모드와 무관하게 답해야 한다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "지금 RUN 이다");
    feed(&h, "ID", 0);
    CHECK(s.n == 2, "RUN 에서도 $ID 는 답한다");
}

static void test_unimplemented_command_is_unsupported(void)
{
    /* 규격 §5 — 조용히 버리면 죽은 링크와 구분되지 않는다.
     *
     * $CFG·$STAT 은 구현돼 있지만 **설정 저장소가 붙어 있을 때만** 답할 수
     * 있다. 저장소 없이 `setup()` 만 한 상태가 그 경계다 — 부팅 중 flash 를
     * 아직 못 읽었거나 저장소가 깨진 경우가 여기 해당한다. 그때 죽거나
     * 침묵하지 않고 UNSUPPORTED 를 돌려준다. */
    MkHostlink h; Sink s;

    setup(&h, &s);
    feed(&h, "CFG,LIST", 1000);
    CHECK(s.n == 1, "한 줄만 낸다");
    if (s.n == 1) {
        CHECK_EQ(s.lines[0], expect_line("SACK,CFG,ERR,UNSUPPORTED"), "CFG 는 UNSUPPORTED");
    }

    setup(&h, &s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 1, "STAT 도 한 줄");
    if (s.n == 1) {
        CHECK_EQ(s.lines[0], expect_line("SACK,STAT,ERR,UNSUPPORTED"), "STAT 는 UNSUPPORTED");
    }
}

static void test_unknown_verb_is_unsupported(void)
{
    /* 오타든 미구현이든 호스트가 할 일은 같다 (§5). */
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "NOPE", 1000);
    CHECK(s.n == 1, "모르는 verb 에도 답한다");
}

static void test_bad_checksum_on_real_command_gets_sack(void)
{
    /* $HB 가 아닌 명령은 체크섬이 틀리면 SACK 를 보낸다 (§3).
     * verb 를 읽을 수 있어야 보낼 수 있다 — Task 1 에서 파서 순서를
     * 바꾼 이유가 이것이다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    mk_hostlink_feed(&h, "$ID*FF\r\n", 8, 1000);
    CHECK(s.n == 1, "체크섬 틀린 $ID 에 한 줄 답한다");
    if (s.n == 1) {
        CHECK_EQ(s.lines[0], expect_line("SACK,ID,ERR,CHECKSUM"), "ERR,CHECKSUM");
    }
}

static void test_malformed_line_is_silent(void)
{
    /* verb 를 읽을 수 없으면 조용히 버린다 (§3). SACK 에 실을 것이 없다. */
    MkHostlink h; Sink s;

    setup(&h, &s);
    mk_hostlink_feed(&h, "garbage\r\n", 9, 1000);
    CHECK(s.n == 0, "$ 없는 줄은 조용히 버린다");

    setup(&h, &s);
    mk_hostlink_feed(&h, "$*00\r\n", 6, 1000);
    CHECK(s.n == 0, "빈 payload 도 조용히");

    setup(&h, &s);
    mk_hostlink_feed(&h, "", 0, 1000);
    CHECK(s.n == 0, "빈 입력도 조용히");
}

static void test_unsendable_verb_is_dropped(void)
{
    /* 🔴 체크섬이 틀린 줄에서 건져낸 verb 를 그대로 SACK 에 실으면,
     *    그 verb 에 제어문자가 있을 때 줄이 쪼개진다. mk_build_line 이
     *    거부하므로 아무것도 나가지 않아야 한다 — 반쪽짜리 줄보다 낫다.
     *    (호스트 쪽 시뮬레이터에서 같은 결함을 실제로 겪었다.) */
    MkHostlink h; Sink s;
    setup(&h, &s);
    /* payload 안에 탭이 든 줄. 체크섬은 틀리게 둔다. */
    mk_hostlink_feed(&h, "$A\tB*00\r\n", 9, 1000);
    CHECK(s.n == 0, "보낼 수 없는 verb 는 아무것도 내보내지 않는다");
}

/* ---- 하트비트 송신 ---------------------------------------------------- */

static void test_tick_emits_hb_at_1hz(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);

    mk_hostlink_tick(&h, 0);
    CHECK(s.n == 0, "부팅 직후에는 안 보낸다");

    mk_hostlink_tick(&h, 999);
    CHECK(s.n == 0, "999 ms 도 아직");

    mk_hostlink_tick(&h, 1000);
    CHECK(s.n == 1, "1000 ms 에 한 번");
    if (s.n >= 1) {
        CHECK_EQ(s.lines[0], expect_line("HB"), "보내는 줄은 $HB*0A");
    }

    mk_hostlink_tick(&h, 1500);
    CHECK(s.n == 1, "주기 안에서는 더 안 보낸다");

    mk_hostlink_tick(&h, 2000);
    CHECK(s.n == 2, "다음 주기에 한 번 더");
}

static void test_tick_does_not_depend_on_mode(void)
{
    /* 보드 -> 호스트 $HB 는 "보드가 살아 있다" 는 뜻이다 (§6.1).
     * RUN 이든 CONFIG 든 계속 보내야 호스트가 연결을 판단할 수 있다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    CHECK(mk_hostlink_mode(&h, 1000) == MK_MODE_RUN, "RUN 이다");
    mk_hostlink_tick(&h, 1000);
    CHECK(s.n == 1, "RUN 에서도 HB 를 보낸다");
}

/* ---- $CFG (2단계) ------------------------------------------------------ */

static MkCfgItem CFG_ITEMS[4];
static MkConfig  CFG_STORE;
static int       saves;
static int       save_fails;

static const MkFieldBit CFG_FIELDS[] = {
    { 0, "device_id", 0, "보드 식별자", 0 },
    { 3, "raw",       1, "ADS1256 원시 카운트", 0 },
};

/* 🔴 저장되는 **순간의** 값을 붙든다. 저장이 끝나면 화면 값으로 되돌아
 *    오므로, 나중에 표를 들여다보면 무엇이 Flash 로 갔는지 알 수 없다. */
static unsigned saved_out;

static int fake_save(void *ctx)
{
    (void)ctx;
    saves++;
    saved_out = CFG_ITEMS[3].cur.u;
    return save_fails ? -1 : 0;
}

static void setup_cfg(MkHostlink *h, Sink *s)
{
    setup(h, s);
    saves = 0;
    save_fails = 0;
    saved_out = 0u;
    memset(CFG_ITEMS, 0, sizeof CFG_ITEMS);

    CFG_ITEMS[0] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                                .vtype = MK_VT_U16, .min = 10, .max = 10000,
                                .has_min = 1, .has_max = 1,
                                .unit = "ms", .label = "전송 주기" };
    CFG_ITEMS[0].def.u = 100;
    CFG_ITEMS[0].cur.u = 100;

    CFG_ITEMS[1] = (MkCfgItem){ .key = "pwr.5v", .group = "pwr",
                                .vtype = MK_VT_BOOL, .readonly = 1,
                                .interlocked = 1, .label = "5V 전원",
                                .note = "쿨링 팬이 5V 전원에 직결이라 끌 수 없다" };
    CFG_ITEMS[1].def.u = 1;
    CFG_ITEMS[1].cur.u = 1;

    CFG_ITEMS[2] = (MkCfgItem){ .key = "dev.id", .group = "dev",
                                .vtype = MK_VT_STR, .max = 15, .has_max = 1,
                                .label = "장치 ID" };
    CFG_ITEMS[2].def.s[0] = '1';
    CFG_ITEMS[2].cur.s[0] = '1';

    /* 🔴 출력 항목을 하나 둔다. 제어 모드(규격 §6.4)가 무엇을 되돌리는지
     *    보려면 out=1 인 항목이 있어야 한다 — 없으면 TEST 를 드나들어도
     *    아무 일이 없어 시험이 헛돈다. */
    CFG_ITEMS[3] = (MkCfgItem){ .key = "pwr.24v", .group = "pwr",
                                .vtype = MK_VT_BOOL, .out = 1,
                                .label = "24V 전원" };

    CFG_STORE.items = CFG_ITEMS;
    CFG_STORE.count = 4;
    CFG_STORE.dirty = 0;

    mk_hostlink_attach_config(h, &CFG_STORE, CFG_FIELDS, 2, fake_save, NULL);
}

static const char *sack_of(Sink *s)
{
    for (int i = s->n - 1; i >= 0; i--) {
        if (s->lines[i][0] == '$') return s->lines[i];
    }
    return "";
}

static void test_cfg_unsupported_without_a_store(void)
{
    /* 1단계 펌웨어가 이 상태였다. 설정 저장소가 없으면 UNSUPPORTED 다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "CFG,LIST", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,UNSUPPORTED"),
             "저장소가 없으면 UNSUPPORTED");
}

static void test_cfg_list_emits_catalog_then_sack(void)
{
    /* 규격 §5.2 — 본문을 먼저 보내고 $SACK 로 끝낸다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "CFG,LIST", 1000);
    CHECK(s.n == 4 + 2 + 1 + 1, "item 4 + field 2 + end 1 + SACK 1");
    CHECK(s.lines[0][0] == '{', "첫 줄은 NDJSON");
    CHECK(strstr(s.lines[0], "\"type\":\"cfg_item\"") != NULL, "cfg_item");
    CHECK(strstr(s.lines[6], "\"count\":6") != NULL, "cfg_end 의 합계");
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "마지막은 SACK,CFG,OK");
}

static void test_cfg_list_works_in_run_mode(void)
{
    /* 규격 §4 — $CFG,LIST 는 CONFIG 전용이 아니다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "지금 RUN 이다");
    sink_reset(&s);
    feed(&h, "CFG,LIST", 0);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "RUN 에서도 목록은 준다");
}

static void test_cfg_get(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "CFG,GET,tx.period_ms", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    CHECK(strstr(s.lines[0], "\"type\":\"cfg_value\"") != NULL, "cfg_value");
    CHECK(strstr(s.lines[0], "\"cur\":100") != NULL, "현재값");

    sink_reset(&s);
    feed(&h, "CFG,GET,없는키", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,UNKNOWN_KEY"),
             "모르는 키");
    CHECK(s.n == 1, "거부에는 본문이 없다");
}

static void test_cfg_set_requires_config_mode(void)
{
    /* 규격 §4 — $CFG,SET 은 CONFIG 전용이다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "지금 RUN 이다");
    sink_reset(&s);
    feed(&h, "CFG,SET,tx.period_ms,250", 0);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,MODE"), "RUN 이면 MODE");
    CHECK(CFG_ITEMS[0].cur.u == 100u, "값이 바뀌지 않았다");
}

static void test_mode_is_checked_before_the_key(void)
{
    /* 🔴 RUN 모드에서 온 요청은 키가 맞든 틀리든 받지 않는다. 키를 먼저
     *    보면 없는 키에 UNKNOWN_KEY 를 돌려주게 되고, 사용자는 키 이름을
     *    고치려 든다 — 진짜 문제는 GUI 가 하트비트를 못 보내고 있다는 것이다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "CFG,SET,없는키,1", 0);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,MODE"),
             "RUN 에서는 없는 키라도 MODE 가 먼저");
}

static void test_cfg_set_in_config_mode(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "CFG,SET,tx.period_ms,250", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "CONFIG 면 받는다");
    CHECK(CFG_ITEMS[0].cur.u == 250u, "값이 바뀌었다");
}

static void test_cfg_set_reasons_reach_the_host(void)
{
    /* 규격 §5.2 의 사유가 그대로 전달되는지. 사용자가 왜 안 되는지를
     * 알아야 한다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);

    sink_reset(&s);
    feed(&h, "CFG,SET,tx.period_ms,999999", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,RANGE"), "범위 밖");

    sink_reset(&s);
    feed(&h, "CFG,SET,pwr.5v,false", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,INTERLOCK"),
             "인터록이 읽기 전용을 이긴다");

    sink_reset(&s);
    feed(&h, "CFG,SET,없는키,1", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,UNKNOWN_KEY"), "모르는 키");
}

static void test_cfg_save(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);

    /* 🔴 바뀐 것이 없으면 Flash 를 쓰지 않는다. 지웠다 쓰는 것은 수명을
     *    깎는 일이고, 아무것도 안 바뀐 저장은 순수한 손해다. */
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "바뀐 것 없어도 OK");
    CHECK(saves == 0, "바뀐 것이 없으면 쓰지 않는다");

    feed(&h, "CFG,SET,tx.period_ms,250", 1000);
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "저장 성공");
    CHECK(saves == 1, "한 번 썼다");

    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1000);
    CHECK(saves == 1, "이미 저장했으면 다시 쓰지 않는다");
}

static void test_cfg_save_failure_is_reported(void)
{
    /* 🔴 저장이 실패했는데 OK 를 보내면 사용자는 저장된 줄 안다.
     *    전원을 뺐다 꽂으면 설정이 사라져 있다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,tx.period_ms,250", 1000);
    save_fails = 1;
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,BUSY"), "실패를 알린다");
    CHECK(mk_cfg_dirty(&CFG_STORE), "실패했으면 여전히 저장이 필요하다");
}

static void test_cfg_save_requires_config_mode(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 0);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,MODE"), "RUN 이면 MODE");
    CHECK(saves == 0, "쓰지 않았다");
}

static void test_cfg_reset(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,tx.period_ms,250", 1000);
    sink_reset(&s);
    feed(&h, "CFG,RESET", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "초기화");
    CHECK(CFG_ITEMS[0].cur.u == 100u, "기본값으로 돌아갔다");
    CHECK(mk_cfg_dirty(&CFG_STORE), "초기화도 저장이 필요하다");
}

static void test_stat_emits_record_then_sack(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"type\":\"stat\"") != NULL, "stat 레코드");
        CHECK(strstr(s.lines[0], "\"rails\"") != NULL, "rails 가 있다");
        CHECK(strstr(s.lines[0], "\"queues\":[]") != NULL,
              "큐가 아직 없으므로 빈 배열");
        CHECK_EQ(s.lines[1], expect_line("SACK,STAT,OK"), "SACK,STAT,OK");
    }
}

static void test_stat_works_in_run_mode(void)
{
    /* 🔴 CONFIG 전용이 아니다 (규격 §4). RUN 에서 상태를 못 보면 진단할
     *    수 없다 — 유실이 나는 것은 대개 RUN 일 때다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "지금 RUN");
    sink_reset(&s);
    feed(&h, "STAT", 0);
    CHECK(s.n == 2, "RUN 에서도 답한다");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"mode\":\"RUN\"") != NULL, "모드도 RUN");
    }
}

static void test_stat_reports_real_queue_state(void)
{
    /* 🔴 `queues` 가 3단계의 유일한 진단 창구다(규격 §7.4). Q2 에서 UART
     *    유실이 ~2% 났는데 어디서 나는지 몰라 8개 가설이 전부 기각된 채로
     *    미해결이다(CLAUDE.md §1.2). 그 일을 되풀이하지 않으려면 이 값이
     *    실제 큐를 비춰야 한다 — 0 을 채워 보내면 없느니만 못하다. */
    static MkAds ads;
    static MkSample qbuf[8];
    static uint8_t tx[8], rx[8];
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_ads_init(&ads, NULL, tx, rx, sizeof tx, 500);
    mk_ads_attach_queue(&ads, 2, qbuf, 4);
    mk_ads_configure(&ads, 2, 1, 100, 0);
    mk_hostlink_attach_ads(&h, &ads);

    /* 칸 4개에 6개를 밀어 넣어 2개를 버리게 한다. */
    for (int i = 0; i < 6; i++) {
        mk_queue_push(mk_ads_queue(&ads, 2), 1000 + i, i);
    }

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"queues\":[{\"ch\":2,\"depth\":4,\"peak\":4,\"drops\":2}]")
              != NULL, "켜진 채널의 실제 깊이·최고치·유실을 싣는다");
        /* 🔴 `ch` 가 2 다. 배열 첨자(0)가 아니라 채널 번호다 — 꺼진 채널을
         *    건너뛰므로 둘은 다르다. */
        CHECK(strstr(s.lines[0], "\"ch\":0") == NULL,
              "꺼진 채널은 목록에 없다");
    }
}

static void test_stat_fits_with_all_seven_channels(void)
{
    /* 🔴 사용자가 채널을 다 켜는 순간 진단 창구가 닫히면 안 된다.
     *
     *    처음 버퍼를 400 으로 잡았을 때 실제로 그랬다. 7채널을 켜면 stat
     *    레코드가 622 바이트라 만들어지지 못하고 $SACK,STAT,ERR,BUSY 만
     *    나갔다. 유실을 보러 들어온 사람이 아무것도 못 보는 상태다.
     *    되돌림 검사에서 우연히 드러난 것을 시험으로 못박는다. */
    static MkAds ads;
    static MkSample qbuf[MK_ADS_CHANNELS][2];
    static uint8_t tx[8], rx[8];
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_ads_init(&ads, NULL, tx, rx, sizeof tx, 500);
    for (int i = 0; i < MK_ADS_CHANNELS; i++) {
        mk_ads_attach_queue(&ads, i, qbuf[i], 2);
        mk_ads_configure(&ads, i, 1, 100, 0);
        /* 넘치게 밀어 넣어 큰 수가 실리게 한다 */
        for (int k = 0; k < 5; k++) {
            mk_queue_push(mk_ads_queue(&ads, i), 1000 + k, k);
        }
    }
    mk_hostlink_attach_ads(&h, &ads);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "7채널을 다 켜도 본문이 나온다");
    if (s.n == 2) {
        CHECK_EQ(s.lines[1], expect_line("SACK,STAT,OK"), "BUSY 가 아니다");
        CHECK(strstr(s.lines[0], "\"ch\":6") != NULL, "마지막 채널까지 실린다");
        CHECK(strstr(s.lines[0], "\"drops\":3") != NULL, "유실 수도 온전하다");
    }
}

static void test_stat_without_sol_reports_empty_din(void)
{
    /* 🔴 queues 와 같은 규칙 — 안 붙어 있으면 0 을 채워 보내지 않고 빈
     *    배열이다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"din\":[]") != NULL,
              "sol 이 안 붙어 있으면 빈 배열");
    }
}

static void test_stat_reports_real_din_state(void)
{
    /* 🔴 din 은 rails 와 반대로 실측이다(규격 §7.4·§7.6) — mk_solctl 이
     *    EXTI 로 읽은 값을 그대로 싣는다. mk_solctl_prime() 으로 부팅
     *    시각의 실제 핀 상태를 흉내 낸다(bsp/mk_sol.c 가 하는 일과 같다).
     *
     *    raw LOW(0) = 신호 있음 = state 1(켜짐) — 극성 반전은 mk_solctl.c
     *    한 곳에서만 하므로 이 시험이 그 결과만 본다. */
    MkSolCtl sol;
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_solctl_init(&sol, NULL, NULL);   /* 레벨 폴링 없음 — prime() 만 본다 */
    mk_solctl_prime(&sol, MK_SOL_J18, 0, 0);   /* raw LOW  -> state 1 */
    mk_solctl_prime(&sol, MK_SOL_J19, 1, 0);   /* raw HIGH -> state 0 */
    mk_solctl_prime(&sol, MK_SOL_J20, 1, 0);
    mk_hostlink_attach_sol(&h, &sol);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"din\":[{\"connector_id\":18,\"state\":1},"
                     "{\"connector_id\":19,\"state\":0},"
                     "{\"connector_id\":20,\"state\":0}]") != NULL,
              "din 이 실측 상태를 커넥터 번호 순으로 싣는다");
    }
}

static void test_stat_without_an_ads_reports_no_queues(void)
{
    /* 🔴 0 을 채워 보내지 않는다. "채널이 없다" 와 "채널이 있는데 유실이
     *    0" 은 다른 말이고, 유실을 찾는 사람에게는 그 차이가 전부다. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"queues\":[]") != NULL,
              "수집기가 없으면 빈 배열");
    } else {
        CHECK(0, "수집기가 없으면 빈 배열");
    }
}

static void test_stat_unsupported_without_a_store(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,STAT,ERR,UNSUPPORTED"),
             "저장소가 없으면 UNSUPPORTED");
}

static void test_cfg_unknown_subcommand(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "CFG,NOPE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,UNSUPPORTED"),
             "모르는 하위 명령");

    sink_reset(&s);
    feed(&h, "CFG", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,UNKNOWN_KEY"),
             "하위 명령이 아예 없으면");
}

/* ---- $GNSS (Phase 3, 규격 §4.1) — GNSS 모듈에 원시 명령 전달 ------------ */

static int    gnss_sent_calls;
static int    gnss_send_ok = 1;    /* 반환값을 조절해 BUSY 시험에 쓴다 */
static char   gnss_sent_buf[64];
static size_t gnss_sent_len;

static int fake_gnss_send(void *ctx, const char *text, size_t len)
{
    (void)ctx;
    gnss_sent_calls++;
    size_t n = len < sizeof gnss_sent_buf - 1u ? len : sizeof gnss_sent_buf - 1u;
    memcpy(gnss_sent_buf, text, n);
    gnss_sent_buf[n] = '\0';
    gnss_sent_len = len;
    return gnss_send_ok;
}

static void reset_gnss_fake(void)
{
    gnss_sent_calls = 0;
    gnss_send_ok = 1;
    gnss_sent_buf[0] = '\0';
    gnss_sent_len = 0;
}

static void test_gnss_requires_config_mode(void)
{
    /* 규격 §4.1 — $GNSS 는 CONFIG 전용이다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    CHECK(mk_hostlink_mode(&h, 0) == MK_MODE_RUN, "지금 RUN 이다");
    sink_reset(&s);
    feed(&h, "GNSS,LOG GPRMC ONTIME 1", 0);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,ERR,MODE"), "RUN 이면 MODE");
    CHECK(gnss_sent_calls == 0, "모듈로 나가지 않는다");
}

static void test_gnss_forwards_text_verbatim_and_appends_line_end(void)
{
    /* 🔴 원문 그대로 + 줄 끝은 보드가 붙인다(규격 §4.1). */
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS,LOG GPRMC ONTIME 1", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,OK"), "CONFIG 면 받는다");
    CHECK(gnss_sent_calls == 1, "모듈로 한 번 나갔다");
    CHECK_EQ(gnss_sent_buf, "LOG GPRMC ONTIME 1\r\n",
             "인자 그대로 + 보드가 붙인 CR/LF");
}

static void test_gnss_rejects_control_characters(void)
{
    /* 🔴 콤마가 아닌 제어문자(탭)는 mk_parse_line 이 그대로 args[0] 에
     * 담는다 — 여기서 걸러야 한다. 체크섬은 맞게 손으로 계산해 두었다
     * (payload "GNSS,LOG<TAB>A" 의 XOR = 0x29). */
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    mk_hostlink_feed(&h, "$GNSS,LOG\tA*29\r\n", 16, 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,ERR,RANGE"), "제어문자는 RANGE");
    CHECK(gnss_sent_calls == 0, "모듈로 안 나간다");
}

static void test_gnss_rejects_empty_text(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,ERR,RANGE"), "인자가 없으면 RANGE");
    CHECK(gnss_sent_calls == 0, "모듈로 안 나간다");
}

static void test_gnss_forwards_embedded_comma_verbatim(void)
{
    /* 🔴 규격 개정 — $GNSS 는 일반 인자 쪼개기(쉼표 분할)를 거치지
     * 않는다. 쉼표가 있어도 원문 그대로 한 덩어리로 모듈에 나간다. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS,LOG,GPRMC", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,OK"), "쉼표가 있어도 받는다");
    CHECK(gnss_sent_calls == 1, "모듈로 나간다");
    CHECK_EQ(gnss_sent_buf, "LOG,GPRMC\r\n", "쉼표까지 원문 그대로 + CR/LF");
}

static void test_gnss_forwards_the_real_um981_pps_command(void)
{
    /* 🔴 실기기에서 막혔던 그 명령. "CONFIG PPS ENABLE3"(18자)·
     * "CONFIG PPS ENABLE2 GPS"(22자) 로 줄여 보냈더니 모듈이 둘 다
     * "PARSING FAILD FIELD OUT OF RANGE, Too less field!" 로 거부했다 —
     * 파라미터 전부(47자)가 한 덩어리로 와야 한다(docs/datasheet/
     * Unicore_N4_Commands.pdf p.21 §4.3 · p.22 Table 4-6). */
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS,CONFIG PPS ENABLE2 GPS POSITIVE 500000 1000 0 0", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,OK"), "실제 PPS 설정 명령을 받는다");
    CHECK(gnss_sent_calls == 1, "모듈로 나간다");
    CHECK_EQ(gnss_sent_buf, "CONFIG PPS ENABLE2 GPS POSITIVE 500000 1000 0 0\r\n",
             "47자 전체가 그대로 나간다");
}

static void test_gnss_text_at_96_boundary_is_accepted(void)
{
    /* MK_GNSS_TEXT_MAX(96) — 경계는 받는다. */
    MkHostlink h; Sink s;
    char payload[6 + 96 + 1];
    memcpy(payload, "GNSS,", 5);
    memset(payload + 5, 'A', 96);
    payload[5 + 96] = '\0';

    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, payload, 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,OK"), "96바이트 텍스트를 받는다");
    CHECK(gnss_sent_calls == 1, "모듈로 나간다");
    CHECK(gnss_sent_len == 98u, "96바이트 + CR/LF");
}

static void test_gnss_text_over_96_is_silently_dropped(void)
{
    /* 규격 §3.1/§4.1 — MK_GNSS_TEXT_MAX(96)를 넘으면 고정폭 버퍼 규칙대로
     * 조용히 버려진다(mk_framing.c 의 mk_parse_line). 다른 명령의
     * 23바이트 한도(MK_ARG_MAX)와는 별개의 상한이다. */
    MkHostlink h; Sink s;
    char payload[6 + 97 + 1];
    memcpy(payload, "GNSS,", 5);
    memset(payload + 5, 'A', 97);
    payload[5 + 97] = '\0';

    setup(&h, &s);
    reset_gnss_fake();
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, payload, 1000);
    CHECK(s.n == 0, "97바이트를 넘는 텍스트는 조용히 버려진다");
    CHECK(gnss_sent_calls == 0, "모듈로도 안 나간다");
}

static void test_gnss_unsupported_without_a_send_callback(void)
{
    /* mk_hostlink_attach_gnss 를 부르지 않은 빌드(GNSS IO 가 없는 경로) —
     * mk_hostlink_attach_config 를 안 불렀을 때 $CFG 가 UNSUPPORTED 인
     * 것과 같은 결. */
    MkHostlink h; Sink s;
    setup(&h, &s);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS,LOG GPRMC ONTIME 1", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,ERR,UNSUPPORTED"),
             "send 콜백이 없으면 UNSUPPORTED");
}

static void test_gnss_send_failure_is_reported_as_busy(void)
{
    MkHostlink h; Sink s;
    setup(&h, &s);
    reset_gnss_fake();
    gnss_send_ok = 0;
    mk_hostlink_attach_gnss(&h, fake_gnss_send, NULL);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    feed(&h, "GNSS,LOG GPRMC ONTIME 1", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,GNSS,ERR,BUSY"),
             "전송 실패는 BUSY 로 알린다");
}

/* ---- $STAT 의 클럭 출처(규격 §7.4) -------------------------------------- */

/* 🔴 크리스털이 안 떠서 폴백한 보드는 **그 사실을 말할 수 있어야 한다.**
 *
 *    보드는 HSE 를 100 ms 까지만 기다리고 안 뜨면 내부 RC 로 계속 부팅한다
 *    (bsp/mk_clock.c) — 거기서 서면 화면도 시리얼도 안 살아나 원인을 알
 *    방법이 없기 때문이다. 그런데 계속 부팅한 것만으로는 부족하다: 초 안쪽
 *    보간이 ±1 % 로 나빠졌다는 것을 호스트가 모르면, 그 보드의 데이터는
 *    아무 표시 없이 저장된다. 이 시험이 그 경로 전체를 지킨다. */
static void test_stat_reports_the_clock_source(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);

    /* 안 붙였으면 null 이다 — 호스트 시험처럼 클럭 없이 도는 빌드. */
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"clock\":{\"src\":null,\"sysclk_hz\":null}") != NULL,
              "클럭을 안 붙였으면 null — 값을 지어내지 않는다");
    }

    mk_hostlink_attach_clock(&h, "hsi", 64000000u);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"clock\":{\"src\":\"hsi\",\"sysclk_hz\":64000000}")
              != NULL,
              "폴백한 보드는 그것을 그대로 싣는다");
    }

    mk_hostlink_attach_clock(&h, "hse_pll", 64000000u);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"clock\":{\"src\":\"hse_pll\",\"sysclk_hz\":64000000}")
              != NULL,
              "크리스털로 돌면 그것도 그대로");
    }
}

/* ---- $STAT 의 gnss 초기화 진단(규격 §7.4·§4.1.1) ------------------------ */

static void test_stat_gnss_init_fields_default_to_false_without_gnssctl(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"init_sent\":false,\"init_exhausted\":false,"
                     "\"sentence_seen\":false") != NULL,
              "gnssctl 이 안 붙어 있으면 전부 거짓");
    }
}

static void test_stat_gnss_init_fields_reflect_gnssctl(void)
{
    MkGnssCtl gc;
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_gnssctl_init(&gc);
    /* 한 번 시도했지만 아직 문장은 못 받은 상태를 흉내 낸다. */
    mk_gnssctl_tick(&gc, 1, 0, 1000, NULL, NULL);
    mk_hostlink_attach_gnssctl(&h, &gc);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        /* send 가 NULL 이라 실제로는 못 보냈다(attempts==0) — sent 는
         * 거짓, 상한도 아직이다. sentence_seen 도 거짓. */
        CHECK(strstr(s.lines[0],
                     "\"init_sent\":false,\"init_exhausted\":false,"
                     "\"sentence_seen\":false") != NULL,
              "gnssctl 의 지금 상태를 그대로 싣는다");
    }

    /* 이번엔 실제로 보낸 뒤(가짜 send) 문장까지 받은 상태. */
    mk_gnssctl_init(&gc);
    mk_gnssctl_tick(&gc, 1, 0, 1000, fake_gnss_send, NULL);
    mk_gnssctl_tick(&gc, 1, 1, 1100, fake_gnss_send, NULL);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"init_sent\":true,\"init_exhausted\":false,"
                     "\"sentence_seen\":true") != NULL,
              "보내고 받았으면 그대로 반영된다");
    }
}

/* ---- $STAT 의 rtcm_* (규격 §7.4, 2026-08-28) ---------------------------- */

static void test_stat_rtcm_fields_default_to_null_without_router(void)
{
    /* 라우터가 안 붙은 빌드 — 나이만 null(받은 적 없음 ≠ 0ms 전)이고
     * 계수는 0 이다. pps_age_ms/pps_raw_count 와 같은 규칙. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"rtcm_age_ms\":null,\"rtcm_bytes\":0,"
                     "\"rtcm_bad\":0,\"rtcm_drop\":0,\"rtcm_overrun\":0")
              != NULL,
              "라우터가 안 붙어 있으면 나이는 null · 계수는 0");
    }
}

static int rtcm_sink_ok(void *ctx, const char *data, size_t len)
{
    (void)ctx; (void)data; (void)len;
    return 1;
}

static void test_stat_rtcm_fields_reflect_the_router(void)
{
    /* 온전한 프레임 하나를 먹인 라우터를 붙이면 $STAT 이 그 계수를 그대로
     * 싣는다. 프레임 벡터의 출처는 tests/test_rtcm.c 머리말(파이썬 계산). */
    static const uint8_t frame[10] =
        { 0xd3, 0x00, 0x04, 0x3e, 0xd0, 0xaa, 0x55, 0x58, 0x2c, 0x06 };

    MkRtcm r;
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    mk_rtcm_init(&r, rtcm_sink_ok, NULL);
    for (size_t i = 0; i < sizeof frame; i++) {
        mk_rtcm_feed(&r, frame[i], 700);
    }
    mk_rtcm_set_link_overruns(&r, 5u);
    mk_hostlink_attach_rtcm(&h, &r);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0],
                     "\"rtcm_age_ms\":300,\"rtcm_bytes\":10,"
                     "\"rtcm_bad\":0,\"rtcm_drop\":0,\"rtcm_overrun\":5")
              != NULL,
              "라우터의 지금 계수를 그대로 싣는다 — 나이는 now - 마지막 온전 프레임");
    }
}

/* ---- $STAT 의 pps_raw_* vs pps_age_ms (규격 §7.4, 2026-08-19) ----------
 *
 * 🔴 실기기 관측(UM981, 실내·fix 없음)을 그대로 재현한다: PPS 는 TIM8
 *    CCR3·PC8 로 정확히 1초 간격으로 들어오는데(GDB 로 직접 확인) RMC 가
 *    전부 무효(V)라 한 번도 짝지어지지 않았다 — `pps_age_ms` 가 계속
 *    null 이라 "PPS 가 안 온다"로 읽혔다. 배선·캡처·ISR 은 전부 정상이었다.
 *    아래 첫 시험이 이번 작업의 핵심이다(작업 지시 원문). */

static void test_stat_pps_raw_visible_even_when_nmea_never_pairs(void)
{
    MkTimeAx tx;
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_timeax_init(&tx);
    mk_hostlink_attach_timeax(&h, &tx);

    mk_timeax_on_pps(&tx, 1000000ULL);              /* PPS @ 1.000000s — 실재한다 */
    MkGnssRmc invalid_rmc;
    memset(&invalid_rmc, 0, sizeof invalid_rmc);
    invalid_rmc.valid = 0;                          /* 실내라 fix 없음 — RMC 가 V */
    mk_timeax_on_rmc(&tx, &invalid_rmc, 1050000ULL);
    mk_timeax_tick(&tx, 1100000ULL);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(s.n == 2, "본문 1 + SACK 1");
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"pps_age_ms\":null") != NULL,
              "짝지어진 나이는 여전히 null — 지금까지 화면이 보여주던 그 버그");
        CHECK(strstr(s.lines[0], "\"pps_raw_age_ms\":100") != NULL,
              "원시 캡처 나이는 값이 있다 — 펄스는 실제로 왔다");
        CHECK(strstr(s.lines[0], "\"pps_raw_count\":1") != NULL,
              "원시 캡처 1회");
        CHECK(strstr(s.lines[0], "\"pps_unpaired_reason\":\"no_valid_nmea\"") != NULL,
              "짝짓기 실패 이유가 실린다 — RMC 는 왔지만 fix 가 무효했다");
    }
}

static void test_stat_pps_both_missing_when_pps_never_arrives(void)
{
    MkTimeAx tx;
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_timeax_init(&tx);
    mk_hostlink_attach_timeax(&h, &tx);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"pps_age_ms\":null") != NULL, "짝지어진 나이 없음");
        CHECK(strstr(s.lines[0], "\"pps_raw_age_ms\":null") != NULL,
              "PPS 자체가 안 왔으니 원시 나이도 없다");
        CHECK(strstr(s.lines[0], "\"pps_raw_count\":0") != NULL, "원시 캡처 0회");
        CHECK(strstr(s.lines[0], "\"pps_unpaired_reason\":null") != NULL,
              "아직 아무 사건도 없으니 이유도 없다");
    }
}

static void test_stat_pps_both_present_when_locked(void)
{
    MkTimeAx tx;
    MkHostlink h; Sink s;

    setup_cfg(&h, &s);
    mk_timeax_init(&tx);
    mk_hostlink_attach_timeax(&h, &tx);

    mk_timeax_on_pps(&tx, 1000000ULL);
    MkGnssRmc rmc;
    memset(&rmc, 0, sizeof rmc);
    rmc.valid = 1;
    rmc.epoch_ms = 1700000000000LL;
    mk_timeax_on_rmc(&tx, &rmc, 1050000ULL);
    mk_timeax_tick(&tx, 1100000ULL);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    if (s.n == 2) {
        CHECK(strstr(s.lines[0], "\"pps_age_ms\":100") != NULL,
              "짝지어진 나이가 있다");
        CHECK(strstr(s.lines[0], "\"pps_raw_age_ms\":100") != NULL,
              "원시 나이도 있다 — 정상 짝짓기에서는 같은 캡처를 가리켜 값이 같다");
        CHECK(strstr(s.lines[0], "\"pps_raw_count\":1") != NULL, "원시 캡처 1회");
        CHECK(strstr(s.lines[0], "\"pps_unpaired_reason\":null") != NULL,
              "짝지어졌으니 실패 이유는 없다");
    }
}

/* ---- Python 시뮬레이터와 대조할 시나리오 ------------------------------ */

/* 🔴 이 대조가 이 계층에서 가장 값지다. GUI 는 시뮬레이터를 상대로 개발되고
 *    실물 보드에서 돌아야 한다. 둘이 같은 입력에 다르게 답하면, GUI 는
 *    시뮬레이터에서 멀쩡하다가 실기기에서만 틀어진다.
 *
 *    각 줄: 지시\t인자...
 *      FEED  <payload>      완성된 줄을 만들어 넣는다
 *      RAW   <이스케이프>   깨진 줄을 그대로 넣는다
 *      TICK                 주기 처리
 *      AT    <ms>           시각을 옮긴다
 *      MODE                 지금 모드를 찍는다
 *    출력: OUT\t<줄> 또는 MODE\t<CONFIG|RUN> */

static size_t unescape_line(const char *s, char *out, size_t cap)
{
    size_t w = 0u;
    for (const char *p = s; *p && w + 1u < cap; p++) {
        if (p[0] == '\\' && p[1] == 'r') { out[w++] = '\r'; p++; }
        else if (p[0] == '\\' && p[1] == 'n') { out[w++] = '\n'; p++; }
        else if (p[0] == '\\' && p[1] == 't') { out[w++] = '\t'; p++; }
        else out[w++] = *p;
    }
    out[w] = '\0';
    return w;
}

static void print_escaped(const char *s, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if      (s[i] == '\r') printf("\\r");
        else if (s[i] == '\n') printf("\\n");
        else if (s[i] == '\t') printf("\\t");
        else                   putchar(s[i]);
    }
}

typedef struct { const char *op; const char *arg; } Step;

static const Step SCENARIO[] = {
    { "AT",   "0" },      { "MODE", NULL },        /* 부팅 직후 */
    { "AT",   "2000" },   { "FEED", "HB" },   { "MODE", NULL },
    { "AT",   "5000" },   { "MODE", NULL },        /* 정확히 3000 ms */
    { "AT",   "5001" },   { "MODE", NULL },        /* 그 다음 ms */
    { "AT",   "6000" },   { "FEED", "HB" },   { "MODE", NULL },
    { "AT",   "6000" },   { "FEED", "ID" },
    { "AT",   "6000" },   { "RAW",  "$HB*FF\\r\\n" },  { "MODE", NULL },
    { "AT",   "6000" },   { "RAW",  "$ID*FF\\r\\n" },
    { "AT",   "6000" },   { "RAW",  "garbage\\r\\n" },
    { "AT",   "6000" },   { "RAW",  "$*00\\r\\n" },
    { "AT",   "6000" },   { "RAW",  "$A\\tB*00\\r\\n" },
    { "AT",   "6000" },   { "FEED", "NOPE" },
    /* 여기부터는 NDJSON 본문을 내는 명령이다. 값은 양쪽 설정표가 달라
     * 같을 수 없으므로 대조 도구가 **모양**으로 비교한다. 표식을 찍어
     * 문자열 추측 없이 나눌 수 있게 한다. */
    { "MARK", "SHAPES" },
    /* 🔴 명령별로 짝지어 확인하려면 어느 명령의 응답인지 남아야 한다.
     *    한 통에 모아 두면 STAT 응답과 CFG 응답이 뒤바뀌어도 통과한다. */
    { "CMD",  "STAT" },     { "AT", "6000" },   { "FEED", "STAT" },
    { "CMD",  "CFG,LIST" }, { "AT", "6000" },   { "FEED", "CFG,LIST" },
};

static void run_scenario(void)
{
    MkHostlink h;
    Sink s;
    int64_t now = 0;

    /* 🔴 설정 저장소를 붙인 채로 돈다. 안 붙이면 $CFG·$STAT 이 전부
     *    UNSUPPORTED 로 나가고, 대조 도구는 "아직 구현 안 됐다" 를
     *    확인하며 통과한다 — 구현한 뒤에도 그대로 통과한다. */
    setup_cfg(&h, &s);
    for (size_t i = 0; i < sizeof SCENARIO / sizeof *SCENARIO; i++) {
        const Step *st = &SCENARIO[i];
        sink_reset(&s);

        if (strcmp(st->op, "AT") == 0) {
            now = 0;
            for (const char *p = st->arg; *p; p++) now = now * 10 + (*p - '0');
            continue;
        }
        if (strcmp(st->op, "MARK") == 0 || strcmp(st->op, "CMD") == 0) {
            printf("%s\t%s\n", st->op, st->arg);
            continue;
        }
        if (strcmp(st->op, "MODE") == 0) {
            printf("MODE\t%s\n",
                   mk_hostlink_mode(&h, now) == MK_MODE_CONFIG ? "CONFIG" : "RUN");
            continue;
        }
        if (strcmp(st->op, "TICK") == 0) {
            mk_hostlink_tick(&h, now);
        } else if (strcmp(st->op, "FEED") == 0) {
            feed(&h, st->arg, now);
        } else if (strcmp(st->op, "RAW") == 0) {
            char line[256];
            size_t n = unescape_line(st->arg, line, sizeof line);
            mk_hostlink_feed(&h, line, n, now);
        }
        for (int k = 0; k < s.n; k++) {
            printf("OUT\t");
            print_escaped(s.lines[k], strlen(s.lines[k]));
            putchar('\n');
        }
    }
}


/* ---- 제어 모드 (규격 §6.4) ---------------------------------------------- */

static void test_boots_in_active_control_mode(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    /* 🔴 보드는 혼자서도 제 일을 해야 한다. 테스트는 사람이 명시적으로
     *    들어가는 상태다. */
    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_ACTIVE, "부팅 기본값은 ACTIVE");
}

static void test_test_mode_needs_someone_watching(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);

    /* 하트비트 없이 = RUN. TEST 의 안전장치가 하트비트 데드맨이므로,
     * 여기서 들어가면 그 장치가 없는 상태가 된다. */
    sink_reset(&s);
    feed(&h, "MODE,TEST", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,MODE,ERR,MODE"), "RUN 에서는 거부");
    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_ACTIVE, "모드가 안 바뀐다");
}

static void test_entering_and_leaving_test_mode(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);

    sink_reset(&s);
    feed(&h, "MODE,TEST", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,MODE,OK"), "CONFIG 에서는 들어간다");
    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_TEST, "TEST 다");

    sink_reset(&s);
    feed(&h, "MODE,ACTIVE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,MODE,OK"), "되돌아간다");
    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_ACTIVE, "ACTIVE 다");
}

static void test_unknown_control_mode_is_rejected(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);

    sink_reset(&s);
    feed(&h, "MODE,PROBING", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,MODE,ERR,RANGE"), "모르는 모드는 RANGE");
    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_ACTIVE, "모드가 안 바뀐다");
}

static void test_leaving_test_drops_the_outputs(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "MODE,TEST", 1000);
    feed(&h, "CFG,SET,pwr.24v,true", 1000);
    CHECK(CFG_ITEMS[3].cur.u == 1u, "테스트 중에는 켜진다");

    feed(&h, "MODE,ACTIVE", 1000);
    /* 🔴 벤치에서 배선 보려고 연 밸브가 그대로 남으면 안 된다. */
    CHECK(CFG_ITEMS[3].cur.u == 0u, "모드를 벗어나면 기본값으로");
}

static void test_losing_the_host_ends_the_test(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "MODE,TEST", 1000);
    feed(&h, "CFG,SET,pwr.24v,true", 1000);

    /* 🔴 하트비트가 이미 데드맨이다. 사람이 안 보면 테스트 출력은 꺼진다. */
    mk_hostlink_tick(&h, 1000 + MK_HB_TIMEOUT_MS + 1);

    CHECK(mk_hostlink_ctl_mode(&h) == MK_CTL_ACTIVE, "테스트가 끝났다");
    CHECK(CFG_ITEMS[3].cur.u == 0u, "출력이 내려갔다");
}

static void test_active_mode_keeps_outputs_when_the_host_leaves(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,pwr.24v,true", 1000);

    mk_hostlink_tick(&h, 1000 + MK_HB_TIMEOUT_MS + 1);

    /* 운전 중이면 보드가 혼자 돈다 — 호스트가 사라져도 출력은 그대로다. */
    CHECK(CFG_ITEMS[3].cur.u == 1u, "ACTIVE 에서는 유지된다");
}

static void test_test_mode_does_not_save_outputs(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "MODE,TEST", 1000);
    feed(&h, "CFG,SET,pwr.24v,true", 1000);

    saved_out = 2;                       /* 아무 값도 아닌 표식 */
    feed(&h, "CFG,SAVE", 1000);
    /* 🔴 저장되는 순간의 값이 기본값이어야 한다. */
    CHECK(saved_out == 0u, "출력은 기본값으로 저장된다");
    /* 저장이 끝나면 화면에 떠 있는 값으로 돌아온다 — 테스트는 계속된다. */
    CHECK(CFG_ITEMS[3].cur.u == 1u, "저장 뒤에도 테스트 값은 그대로");
}

/* ---- 링크 속도 (규격 §4.2) --------------------------------------------- */

/* 가짜 전선. sink_emit 이 아니라 이쪽을 보는 이유는 아래 순서 시험에 있다. */
static uint32_t WIRE_BAUD;
static MkLinkBaud LB;

static void wire_apply(void *ctx, uint32_t baud)
{
    (void)ctx;
    WIRE_BAUD = baud;
}

/* 🔴 응답이 **어느 속도로** 나갔는지를 기록하는 통. 순서를 시험하려면
 *    "무엇을 보냈나" 만으로는 부족하고 "보낼 때 전선이 몇이었나" 가
 *    있어야 한다. */
#define BAUD_LOG_CAP 8
static uint32_t BAUD_AT_EMIT[BAUD_LOG_CAP];
static int      BAUD_LOG_N;

static void sink_emit_logging_baud(void *ctx, const char *line, size_t len)
{
    if (BAUD_LOG_N < BAUD_LOG_CAP) {
        BAUD_AT_EMIT[BAUD_LOG_N++] = WIRE_BAUD;
    }
    sink_emit(ctx, line, len);
}

static const uint32_t LINK_CHOICES[] = { 115200u, 460800u, 921600u,
                                         1000000u, 1500000u, 2000000u };

/* `link.baud` 항목을 CFG_ITEMS 에 하나 더 붙인 설정표. */
static MkCfgItem LINK_ITEMS[5];
static MkConfig  LINK_STORE;
static int       link_saves;

static int link_save(void *ctx) { (void)ctx; link_saves++; return 0; }

static void setup_link(MkHostlink *h, Sink *s)
{
    sink_reset(s);
    BAUD_LOG_N = 0;
    link_saves = 0;
    memset(LINK_ITEMS, 0, sizeof LINK_ITEMS);

    LINK_ITEMS[0] = (MkCfgItem){ .key = "link.baud", .group = "link",
                                 .vtype = MK_VT_ENUM,
                                 .choices = LINK_CHOICES, .n_choices = 6,
                                 .unit = "bps", .label = "호스트 링크 속도" };
    LINK_ITEMS[0].def.u = MK_LINKBAUD_DEFAULT;
    LINK_ITEMS[0].cur.u = MK_LINKBAUD_DEFAULT;
    LINK_ITEMS[1] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                                 .vtype = MK_VT_U16, .min = 10, .max = 10000,
                                 .has_min = 1, .has_max = 1,
                                 .label = "전송 주기" };
    LINK_ITEMS[1].def.u = 100;
    LINK_ITEMS[1].cur.u = 100;

    LINK_STORE.items = LINK_ITEMS;
    LINK_STORE.count = 2;
    LINK_STORE.dirty = 0;

    WIRE_BAUD = MK_LINKBAUD_DEFAULT;
    mk_linkbaud_init(&LB, 64000000u, MK_LINKBAUD_DEFAULT);

    mk_hostlink_init(h, sink_emit_logging_baud, s, "1", "0.1.0", "2.0");
    mk_hostlink_attach_config(h, &LINK_STORE, CFG_FIELDS, 2, link_save, NULL);
    mk_hostlink_attach_linkbaud(h, &LB);
}

static void test_sack_goes_out_at_the_old_baud(void)
{
    /* 🔴 규격 §4.2.2 규칙 1 — **이 시험이 이 기능의 본체다.**
     *
     *    순서를 뒤집으면 응답의 앞부분만 옛 속도로 나가 호스트가 그것을
     *    읽지 못하고, 성공했는지조차 모르는 채로 링크가 끊긴다. 그리고
     *    그 상태는 굽기로만 풀린다.
     *
     *    순서를 지키는 구조는 "요청을 받는 곳(feed)" 과 "속도를 바꾸는
     *    곳(tick)" 이 아예 다른 함수라는 것이다. 그래서 여기서는 두
     *    시점 사이에서 전선을 들여다본다. */
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    sink_reset(&s);
    BAUD_LOG_N = 0;

    feed(&h, "CFG,SET,link.baud,1500000", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "받아들인다");
    CHECK(BAUD_LOG_N == 1, "응답 한 줄이 나갔다");
    CHECK(BAUD_AT_EMIT[0] == MK_LINKBAUD_DEFAULT,
          "🔴 그 줄은 **옛 속도로** 나갔다");
    CHECK(WIRE_BAUD == MK_LINKBAUD_DEFAULT, "feed 는 절대 속도를 바꾸지 않는다");

    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);
    CHECK(WIRE_BAUD == 1500000u, "속도는 그 뒤에 바뀐다");
}

static void test_confirm_commits_and_unblocks_save(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,link.baud,1500000", 1000);
    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);

    /* 확인 전에는 저장이 막힌다 (규격 §4.2.2 규칙 5). */
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,ERR,BUSY"),
             "🔴 확정 전에는 Flash 에 안 쓴다");
    CHECK(link_saves == 0, "저장 콜백도 안 불렸다");

    sink_reset(&s);
    feed(&h, "BAUD,CONFIRM,1500000", 1500);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,OK"), "확인이 받아들여진다");

    sink_reset(&s);
    feed(&h, "CFG,SAVE", 1500);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "확정된 뒤에는 저장된다");
    CHECK(link_saves == 1, "저장 콜백이 불렸다");
}

static void test_confirm_works_in_run_mode(void)
{
    /* 🔴 규격 §4.2.3 — CONFIG 전용이 아니다.
     *
     *    호스트는 포트를 닫았다 새 속도로 다시 여는 동안 $HB 를 못 보내고,
     *    그것이 3000 ms 를 넘으면 보드는 RUN 으로 떨어진다. CONFIG 전용
     *    으로 두면 포트를 여는 데 오래 걸린 것만으로 확인이 거부되고,
     *    그 거부가 곧 우리가 막으려던 되돌림을 부른다. */
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,link.baud,460800", 1000);
    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);

    /* 하트비트가 끊겨 RUN 으로 떨어진 뒤에 확인이 온다. */
    CHECK(mk_hostlink_mode(&h, 6000) == MK_MODE_RUN, "먼저 RUN 으로 떨어졌다");
    sink_reset(&s);
    feed(&h, "BAUD,CONFIRM,460800", 6000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,OK"), "RUN 에서도 확인된다");
}

static void test_confirm_rejects_a_different_value(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,link.baud,460800", 1000);
    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);

    sink_reset(&s);
    feed(&h, "BAUD,CONFIRM,921600", 1200);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,RANGE"),
             "다른 값으로는 확정되지 않는다");
    CHECK(mk_linkbaud_is_pending(&LB), "여전히 대기 중 — 시한은 계속 흐른다");
}

static void test_confirm_without_a_pending_change(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "BAUD,CONFIRM,921600", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,MODE"),
             "확인할 것이 없으면 MODE");
}

static void test_baud_is_unsupported_without_a_state_machine(void)
{
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);                    /* linkbaud 를 안 붙인 빌드 */
    feed(&h, "BAUD,CONFIRM,921600", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,UNSUPPORTED"),
             "링크 속도를 안 붙인 빌드는 UNSUPPORTED");
}

static void test_baud_rejects_garbage(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "BAUD,CONFIRM,abc", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,RANGE"), "숫자가 아니면 RANGE");
    sink_reset(&s);
    feed(&h, "BAUD,CONFIRM", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,RANGE"), "값이 없으면 RANGE");
    sink_reset(&s);
    feed(&h, "BAUD,WHAT,921600", 1000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,BAUD,ERR,UNSUPPORTED"),
             "모르는 하위 명령은 UNSUPPORTED");
}

static void test_deadline_revert_reopens_save(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,link.baud,2000000", 1000);
    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);
    CHECK(WIRE_BAUD == 2000000u, "일단 바뀌었다");

    mk_linkbaud_tick(&LB, &LINK_STORE, 1000 + MK_LINKBAUD_CONFIRM_MS,
                     wire_apply, NULL);
    CHECK(WIRE_BAUD == MK_LINKBAUD_DEFAULT, "시한이 지나 옛 속도로 돌아왔다");
    CHECK(LINK_ITEMS[0].cur.u == MK_LINKBAUD_DEFAULT, "설정표도 따라 돌아왔다");

    /* 되돌아온 뒤에는 저장이 다시 열린다 — 옛(확정된) 속도가 저장된다. */
    feed(&h, "HB", 11000);
    sink_reset(&s);
    feed(&h, "CFG,SAVE", 11000);
    CHECK_EQ(sack_of(&s), expect_line("SACK,CFG,OK"), "되돌아온 뒤에는 저장된다");
}

static void test_stat_carries_the_link_state(void)
{
    MkHostlink h; Sink s;
    setup_link(&h, &s);
    feed(&h, "HB", 1000);
    feed(&h, "CFG,SET,link.baud,1000000", 1000);
    mk_linkbaud_tick(&LB, &LINK_STORE, 1000, wire_apply, NULL);

    sink_reset(&s);
    feed(&h, "STAT", 1000);
    CHECK(strstr(s.lines[0], "\"link\":{\"baud\":1000000,"
                             "\"confirmed\":921600,\"pending\":1000000,"
                             "\"remaining_ms\":10000,\"applied\":1,"
                             "\"confirmed_count\":0,\"reverted\":0}") != NULL,
          "확인 대기 상태가 그대로 나간다");
}

static void test_stat_link_is_null_without_a_state_machine(void)
{
    /* 🔴 속도를 지어내지 않는다 — clock 과 같은 결. */
    MkHostlink h; Sink s;
    setup_cfg(&h, &s);
    feed(&h, "STAT", 1000);
    CHECK(strstr(s.lines[0], "\"link\":{\"baud\":null,\"confirmed\":null,"
                             "\"pending\":null,\"remaining_ms\":null,"
                             "\"applied\":0,\"confirmed_count\":0,"
                             "\"reverted\":0}") != NULL,
          "안 붙어 있으면 속도가 null 이다");
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--scenario") == 0) {
        run_scenario();
        return 0;
    }
    printf("mk_hostlink\n");
    test_boots_in_run();
    test_hb_opens_config();
    test_mode_boundary_is_strictly_greater();
    test_hb_refreshes();
    test_broken_hb_does_not_open_config();
    test_broken_hb_sends_no_sack();
    test_good_hb_sends_no_sack();
    test_id_emits_record_then_sack();
    test_id_does_not_claim_ok_when_record_does_not_fit();
    test_id_record_at_buffer_boundary();
    test_null_emit_does_not_crash();
    test_id_works_in_run_mode();
    test_unimplemented_command_is_unsupported();
    test_unknown_verb_is_unsupported();
    test_bad_checksum_on_real_command_gets_sack();
    test_malformed_line_is_silent();
    test_unsendable_verb_is_dropped();
    test_cfg_unsupported_without_a_store();
    test_cfg_list_emits_catalog_then_sack();
    test_cfg_list_works_in_run_mode();
    test_cfg_get();
    test_cfg_set_requires_config_mode();
    test_mode_is_checked_before_the_key();
    test_cfg_set_in_config_mode();
    test_cfg_set_reasons_reach_the_host();
    test_cfg_save();
    test_cfg_save_failure_is_reported();
    test_cfg_save_requires_config_mode();
    test_cfg_reset();
    test_stat_emits_record_then_sack();
    test_stat_works_in_run_mode();
    test_stat_reports_real_queue_state();
    test_stat_fits_with_all_seven_channels();
    test_stat_without_sol_reports_empty_din();
    test_stat_reports_real_din_state();
    test_stat_without_an_ads_reports_no_queues();
    test_stat_unsupported_without_a_store();
    test_cfg_unknown_subcommand();
    test_gnss_requires_config_mode();
    test_gnss_forwards_text_verbatim_and_appends_line_end();
    test_gnss_rejects_control_characters();
    test_gnss_rejects_empty_text();
    test_gnss_forwards_embedded_comma_verbatim();
    test_gnss_forwards_the_real_um981_pps_command();
    test_gnss_text_at_96_boundary_is_accepted();
    test_gnss_text_over_96_is_silently_dropped();
    test_gnss_unsupported_without_a_send_callback();
    test_gnss_send_failure_is_reported_as_busy();
    test_stat_reports_the_clock_source();
    test_stat_gnss_init_fields_default_to_false_without_gnssctl();
    test_stat_gnss_init_fields_reflect_gnssctl();
    test_stat_rtcm_fields_default_to_null_without_router();
    test_stat_rtcm_fields_reflect_the_router();
    test_stat_pps_raw_visible_even_when_nmea_never_pairs();
    test_stat_pps_both_missing_when_pps_never_arrives();
    test_stat_pps_both_present_when_locked();
    test_tick_emits_hb_at_1hz();
    test_tick_does_not_depend_on_mode();
    test_boots_in_active_control_mode();
    test_test_mode_needs_someone_watching();
    test_entering_and_leaving_test_mode();
    test_unknown_control_mode_is_rejected();
    test_leaving_test_drops_the_outputs();
    test_losing_the_host_ends_the_test();
    test_active_mode_keeps_outputs_when_the_host_leaves();
    test_test_mode_does_not_save_outputs();
    test_sack_goes_out_at_the_old_baud();
    test_confirm_commits_and_unblocks_save();
    test_confirm_works_in_run_mode();
    test_confirm_rejects_a_different_value();
    test_confirm_without_a_pending_change();
    test_baud_is_unsupported_without_a_state_machine();
    test_baud_rejects_garbage();
    test_deadline_revert_reopens_save();
    test_stat_carries_the_link_state();
    test_stat_link_is_null_without_a_state_machine();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
