/* mk_hostlink 단위 시험. 보드도 크로스 툴체인도 필요 없다.
 *
 * 시각을 손으로 돌려가며 3초 타임아웃과 1 Hz 하트비트를 시험한다.
 * 실기기에서 3초를 기다릴 필요가 없다는 것이 HAL 비의존의 값이다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_hostlink.h"
#include "../app/mk_framing.h"

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

#define CAP_LINES 16
typedef struct {
    char lines[CAP_LINES][256];
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
     * 1단계 펌웨어는 $CFG 도 $STAT 도 아직 없다. */
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
    /* 여기부터는 1단계가 아직 구현하지 않은 명령이라 양쪽 응답이 다르다.
     * 표식을 찍어 대조 도구가 문자열 추측 없이 나눌 수 있게 한다. */
    { "MARK", "GAPS" },
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

    setup(&h, &s);
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
    test_tick_emits_hb_at_1hz();
    test_tick_does_not_depend_on_mode();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
