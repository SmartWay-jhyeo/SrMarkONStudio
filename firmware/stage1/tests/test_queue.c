/* mk_queue 단위 시험. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_queue.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static MkSample BUF[4];
static MkQueue Q;

static void setup(void) { mk_queue_init(&Q, BUF, 4); }

static void test_starts_empty(void)
{
    setup();
    CHECK(mk_queue_count(&Q) == 0, "처음엔 비어 있다");
    CHECK(mk_queue_pop(&Q, NULL) == 0, "빈 큐에서 꺼내면 실패");
    CHECK(mk_queue_drops(&Q) == 0, "버린 것도 없다");
}

static void test_fifo_order(void)
{
    setup();
    for (int i = 0; i < 3; i++) {
        mk_queue_push(&Q, 1000 + i, 100 + i);
    }
    MkSample s;
    int ok = 1;
    for (int i = 0; i < 3; i++) {
        if (!mk_queue_pop(&Q, &s) || s.t_ms != 1000 + i || s.raw != 100 + i) {
            ok = 0;
        }
    }
    CHECK(ok, "넣은 순서대로 나온다");
    CHECK(mk_queue_count(&Q) == 0, "다 꺼내면 빈다");
}

static void test_wraps_around(void)
{
    /* 링이 한 바퀴 돌아도 순서가 유지되는지. 넣고 빼고를 섞어 head 와
     * tail 이 서로를 넘어가게 만든다. */
    setup();
    MkSample s;
    for (int round = 0; round < 10; round++) {
        mk_queue_push(&Q, round, round);
        if (!mk_queue_pop(&Q, &s) || s.t_ms != round) {
            printf("  FAIL 한 바퀴 돈 뒤 순서 (round %d)\n", round);
            failures++;
            return;
        }
    }
    CHECK(1, "한 바퀴 돌아도 순서가 유지된다");
    CHECK(mk_queue_drops(&Q) == 0, "넘치지 않았으니 버린 것이 없다");
}

static void test_full_drops_the_oldest(void)
{
    /* 🔴 가장 오래된 것을 버린다. 새것을 버리지 않는다.
     *    호스트가 잠깐 느려졌을 때 보고 싶은 것은 지금 값이다. */
    setup();
    for (int i = 0; i < 6; i++) {        /* 칸은 4개 */
        mk_queue_push(&Q, 1000 + i, i);
    }
    CHECK(mk_queue_count(&Q) == 4, "칸 수를 넘지 않는다");
    CHECK(mk_queue_drops(&Q) == 2, "넘친 2개를 세었다");

    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s.t_ms == 1002, "가장 오래된 둘이 버려지고 3번째가 맨 앞");

    /* 마지막에 넣은 것이 살아 있어야 한다 — 이것이 요점이다. */
    MkSample last = {0, 0};
    while (mk_queue_pop(&Q, &s)) { last = s; }
    CHECK(last.t_ms == 1005, "가장 최근 표본이 살아남는다");
}

static void test_push_reports_whether_it_dropped(void)
{
    setup();
    int all_ok = 1;
    for (int i = 0; i < 4; i++) {
        if (mk_queue_push(&Q, i, i) != 1) { all_ok = 0; }
    }
    CHECK(all_ok, "여유가 있으면 1 을 돌려준다");
    CHECK(mk_queue_push(&Q, 99, 99) == 0, "버렸으면 0 을 돌려준다");
}

static void test_peak_records_the_high_water_mark(void)
{
    /* 🔴 peak 이 있어야 "여유가 얼마나 빠듯했는지" 를 사후에 알 수 있다.
     *    지금 깊이만 보면 이미 빠져나간 뒤라 늘 0 으로 보인다. */
    setup();
    for (int i = 0; i < 3; i++) { mk_queue_push(&Q, i, i); }
    MkSample s;
    while (mk_queue_pop(&Q, &s)) { }
    CHECK(mk_queue_count(&Q) == 0, "지금은 비었지만");
    CHECK(mk_queue_peak(&Q) == 3, "최고 3까지 찼던 것이 남아 있다");
}

static void test_clear_keeps_the_diagnostics(void)
{
    /* 🔴 큐를 비우는 것은 흔한 일이다(재연결, 설정 변경). 그때마다 유실
     *    이력이 사라지면 "아까 유실이 있었나" 를 영영 알 수 없다. */
    setup();
    for (int i = 0; i < 6; i++) { mk_queue_push(&Q, i, i); }
    mk_queue_clear(&Q);
    CHECK(mk_queue_count(&Q) == 0, "비운다");
    CHECK(mk_queue_drops(&Q) == 2, "버린 기록은 남는다");
    CHECK(mk_queue_peak(&Q) == 4, "최고치도 남는다");

    mk_queue_reset_stats(&Q);
    CHECK(mk_queue_drops(&Q) == 0, "따로 부르면 그때 지운다");
}

static void test_no_storage_still_counts_drops(void)
{
    /* 저장소를 안 붙였는데 조용히 0 을 돌려주면 "유실이 없었다" 로 보인다. */
    MkQueue q;
    mk_queue_init(&q, NULL, 0);
    mk_queue_push(&q, 1, 1);
    mk_queue_push(&q, 2, 2);
    CHECK(mk_queue_drops(&q) == 2, "저장소가 없어도 버린 수는 센다");
    CHECK(mk_queue_count(&q) == 0, "들어간 것은 없다");
}

static void test_negative_raw_survives(void)
{
    /* 🔴 raw 는 24비트 2의 보수를 부호확장한 값이다. 부호 없는 타입에
     *    담으면 음수가 800만 근처의 큰 수로 보인다. */
    setup();
    mk_queue_push(&Q, 0, -8388608);      /* 24비트 최소 */
    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s.raw == -8388608, "음수 원시값이 그대로 나온다");
}

static void test_timestamp_is_64bit(void)
{
    /* 🔴 획득 시각은 UTC epoch ms 가 될 수 있다(규격 §7.1.2). 32비트로는
     *    2038년에 넘친다. 지금 device_clock 이라고 32비트로 두면 GNSS 가
     *    붙는 3단계에서 조용히 잘린다. */
    setup();
    const int64_t epoch = 1772200855875LL;      /* 32비트를 넘는 값 */
    mk_queue_push(&Q, epoch, 1);
    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s.t_ms == epoch, "epoch ms 가 잘리지 않는다");
}

/* ---- 임계구역 훅 (검토 지적 I3) ------------------------------------------
 *
 * 🔴 경합 자체(ISR 이 pop 의 로드-스토어 사이에 끼는 것)는 이 호스트에서
 *    진짜로 재현하기 어렵다 — 단일 스레드라 두 함수가 동시에 돌 방법이
 *    없다. 대신 못박는 것은 두 가지다.
 *
 *    1) push·pop 이 실제로 진입/이탈 훅을 부르는가 — 이것이 서 있어야
 *       bsp/mk_critsec.c 가 무엇을 감싸는지가 뜻을 갖는다. 보호를 감싼
 *       s_crit_enter()/s_crit_exit() 호출을 mk_queue.c 에서 지우면 이
 *       시험이 즉시 깨진다(되돌림 검사로 실제 확인했다).
 *    2) 훅이 실제로 꽂혀 있어도(무보호 기본값이 아니어도) FIFO·drops·
 *       peak 규칙이 그대로인가 — wrapping 자체가 로직을 바꾸지 않았는지. */

static int s_crit_enter_calls;
static int s_crit_exit_calls;

static void counting_enter(void) { s_crit_enter_calls++; }
static void counting_exit(void)  { s_crit_exit_calls++; }

static void test_push_and_pop_invoke_the_critical_section(void)
{
    setup();
    s_crit_enter_calls = 0;
    s_crit_exit_calls = 0;
    mk_queue_set_critical_section(counting_enter, counting_exit);

    mk_queue_push(&Q, 1, 1);
    CHECK(s_crit_enter_calls == 1 && s_crit_exit_calls == 1,
          "push 가 진입·이탈을 정확히 한 번씩 부른다");

    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s_crit_enter_calls == 2 && s_crit_exit_calls == 2,
          "pop 도 진입·이탈을 정확히 한 번씩 부른다");

    /* 🔴 빈 큐로 일찍 돌아가는 경로에서도 이탈을 건너뛰면 안 된다 —
     *    건너뛰면 다음 진입 때 저장된 PRIMASK 가 어긋난다(mk_critsec.c). */
    mk_queue_pop(&Q, &s);
    CHECK(s_crit_enter_calls == 3 && s_crit_exit_calls == 3,
          "빈 큐에서 일찍 돌아가도 진입·이탈이 짝을 이룬다");

    mk_queue_set_critical_section(NULL, NULL);   /* 다음 시험에 새지 않게 */
}

static void test_behaviour_is_unchanged_with_hooks_installed(void)
{
    setup();
    mk_queue_set_critical_section(counting_enter, counting_exit);

    for (int i = 0; i < 6; i++) { mk_queue_push(&Q, 1000 + i, i); }   /* 칸은 4개 */
    CHECK(mk_queue_count(&Q) == 4, "훅이 있어도 칸 수를 넘지 않는다");
    CHECK(mk_queue_drops(&Q) == 2, "훅이 있어도 넘친 2개를 센다");
    CHECK(mk_queue_peak(&Q) == 4, "훅이 있어도 최고치가 맞다");

    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s.t_ms == 1002, "훅이 있어도 가장 오래된 둘이 버려지고 3번째가 맨 앞");

    MkSample last = {0, 0};
    while (mk_queue_pop(&Q, &s)) { last = s; }
    CHECK(last.t_ms == 1005, "훅이 있어도 가장 최근 표본이 살아남는다");

    mk_queue_set_critical_section(NULL, NULL);
}

static void test_null_hooks_restore_the_noop_default(void)
{
    /* 🔴 NULL 을 등록하면 무보호 기본값으로 돌아가야 한다 — 시험끼리
     *    상태가 새지 않는다는 계약이고, 위 두 시험이 끝에 이걸 쓴다. */
    setup();
    mk_queue_set_critical_section(counting_enter, counting_exit);
    mk_queue_set_critical_section(NULL, NULL);

    s_crit_enter_calls = 0;
    s_crit_exit_calls = 0;
    mk_queue_push(&Q, 1, 1);
    MkSample s;
    mk_queue_pop(&Q, &s);
    CHECK(s_crit_enter_calls == 0 && s_crit_exit_calls == 0,
          "NULL,NULL 등록 뒤에는 훅이 다시 불리지 않는다");
}

int main(void)
{
    printf("mk_queue\n");
    test_starts_empty();
    test_fifo_order();
    test_wraps_around();
    test_full_drops_the_oldest();
    test_push_reports_whether_it_dropped();
    test_peak_records_the_high_water_mark();
    test_clear_keeps_the_diagnostics();
    test_no_storage_still_counts_drops();
    test_negative_raw_survives();
    test_timestamp_is_64bit();
    test_push_and_pop_invoke_the_critical_section();
    test_behaviour_is_unchanged_with_hooks_installed();
    test_null_hooks_restore_the_noop_default();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
