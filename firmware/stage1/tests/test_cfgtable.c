/* 실제로 보드에 실리는 45항목 설정표를 시험한다.
 *
 * 🔴 이 표에는 여태 시험이 하나도 없었다. test_cfgwire.c 는 6항목짜리
 *    시험용 표를 쓰고, crosscheck_cfg.py 는 그 시험용 표를 확인한다.
 *    정작 보드에 실리는 mk_cfgtable.c 는 main.c 에만 엮여 있어서, 여기가
 *    시뮬레이터와 어긋나도 실기기에 굽기 전에는 아무도 몰랐다.
 *
 *    GUI 는 시뮬레이터를 상대로 개발되고 실물 보드에서 돌아야 한다.
 *    두 카탈로그가 어긋나면 GUI 는 시뮬레이터에서 멀쩡하다가 보드에서만
 *    틀어진다 — 그것이 이 파일과 crosscheck_cfgtable.py 가 막는 것이다.
 *
 * `--catalog` 로 돌리면 실제 표의 $CFG,LIST 응답 본문을 그대로 찍는다.
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_cfgtable.h"
#include "../app/mk_cfgwire.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static MkConfig CFG;

static void setup(void) { mk_cfgtable_init(&CFG); }

/* ---- 표 자체 ------------------------------------------------------------ */

static void test_every_key_is_unique(void)
{
    /* 🔴 키가 겹치면 mk_cfg_find 가 앞의 것만 찾는다. 뒤엣것은 $CFG,SET 으로
     *    영원히 못 바꾸는데, 카탈로그에는 두 번 나와서 화면에는 보인다. */
    setup();
    int dups = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        for (size_t k = i + 1u; k < CFG.count; k++) {
            if (strcmp(CFG.items[i].key, CFG.items[k].key) == 0) {
                printf("       중복: %s\n", CFG.items[i].key);
                dups++;
            }
        }
    }
    CHECK(dups == 0, "키가 겹치지 않는다");
}

static void test_every_item_has_a_label(void)
{
    /* 화면은 key 가 아니라 label 을 보여 준다. 없으면 사용자가 `ain3.span`
     * 같은 것을 읽게 된다. */
    setup();
    int missing = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        if (CFG.items[i].label == NULL || CFG.items[i].label[0] == '\0') {
            printf("       라벨 없음: %s\n", CFG.items[i].key);
            missing++;
        }
    }
    CHECK(missing == 0, "모든 항목에 라벨이 있다");
}

static void test_no_pin_names_anywhere(void)
{
    /* 🔴 설계 원칙 1 (CLAUDE.md §3) — 화면과 프로토콜은 `PD8` 이 아니라
     *    `24V 전원` 을 쓴다. 한 번 어겼다가 GUI 를 띄워 보고서야 찾았다.
     *    사람 눈 대신 여기서 잡는다. */
    static const char *PORTS = "ABCDEFGH";
    setup();
    int found = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const char *texts[3] = { CFG.items[i].label, CFG.items[i].note,
                                 CFG.items[i].unit };
        for (int t = 0; t < 3; t++) {
            const char *s = texts[t];
            if (s == NULL) continue;
            for (const char *p = s; p[0] && p[1] && p[2]; p++) {
                /* P<포트문자><숫자> 꼴 — PD8, PA15 */
                if (p[0] == 'P' && strchr(PORTS, p[1]) != NULL
                    && p[2] >= '0' && p[2] <= '9') {
                    printf("       핀 이름: %s -> \"%s\"\n", CFG.items[i].key, s);
                    found++;
                }
            }
        }
    }
    CHECK(found == 0, "핀 번호가 어디에도 없다");
}

static void test_defaults_are_inside_their_own_range(void)
{
    /* 🔴 기본값이 범위 밖이면 $CFG,RESET 직후 설정이 부적합해진다.
     *    사용자는 초기화했는데 화면이 빨개지는 것을 보게 된다. */
    setup();
    int bad = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const MkCfgItem *it = &CFG.items[i];
        double v;
        if (it->vtype == MK_VT_F32)      v = (double)it->def.f;
        else if (it->vtype == MK_VT_STR) continue;
        else                             v = (double)it->def.u;

        if ((it->has_min && v < it->min) || (it->has_max && v > it->max)) {
            printf("       범위 밖 기본값: %s = %g [%g, %g]\n",
                   it->key, v, it->min, it->max);
            bad++;
        }
    }
    CHECK(bad == 0, "모든 기본값이 자기 범위 안에 있다");
}

static void test_current_starts_at_default(void)
{
    setup();
    int bad = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const MkCfgItem *it = &CFG.items[i];
        int same = (it->vtype == MK_VT_STR)
                   ? strcmp(it->cur.s, it->def.s) == 0
                   : (it->vtype == MK_VT_F32 ? it->cur.f == it->def.f
                                             : it->cur.u == it->def.u);
        if (!same) { printf("       %s\n", it->key); bad++; }
    }
    CHECK(bad == 0, "초기화 직후 현재값 = 기본값");
    CHECK(!mk_cfg_dirty(&CFG), "초기화 직후에는 저장할 것이 없다");
}

static void test_enums_carry_choices(void)
{
    /* 🔴 없으면 호스트가 콤보를 만들 수 없다(규격 §7.3). 항목을
     *    하드코딩하지 않으므로 허용값도 보드가 알려 줘야 한다. */
    setup();
    int bad = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const MkCfgItem *it = &CFG.items[i];
        if (it->vtype != MK_VT_ENUM) continue;
        if (it->n_choices == 0 || it->choices == NULL) {
            printf("       허용값 없음: %s\n", it->key);
            bad++;
        }
    }
    CHECK(bad == 0, "enum 은 모두 허용값을 들고 있다");
}

static void test_enum_defaults_are_choices(void)
{
    setup();
    int bad = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const MkCfgItem *it = &CFG.items[i];
        if (it->vtype != MK_VT_ENUM || it->choices == NULL) continue;
        int hit = 0;
        for (size_t c = 0; c < it->n_choices; c++) {
            if (it->choices[c] == it->def.u) hit = 1;
        }
        if (!hit) { printf("       %s = %u\n", it->key, it->def.u); bad++; }
    }
    CHECK(bad == 0, "enum 기본값이 허용값 안에 있다");
}

static void test_interlocked_items_say_why(void)
{
    /* 화면은 ro 로 비활성만 정하고 **사유는 note 가 말한다**(규격 §7.3).
     * 사유 없이 비활성이면 사용자는 고장인 줄 안다. */
    setup();
    int bad = 0;
    for (size_t i = 0; i < CFG.count; i++) {
        const MkCfgItem *it = &CFG.items[i];
        if (!it->interlocked) continue;
        if (it->note == NULL || it->note[0] == '\0') {
            printf("       사유 없음: %s\n", it->key);
            bad++;
        }
    }
    CHECK(bad == 0, "인터록 항목은 사유를 함께 낸다");
}

static void test_five_volt_rail_is_settable_but_warns(void)
{
    /* 사용자 확정(2026-08-14) — 5V 도 끌 수 있어야 한다.
     *
     * 🔴 막지 않는 대신 **무엇이 함께 멈추는지 알려 준다.** 쿨링 팬(J34)이
     *    직결이고 ADS1256 의 아날로그 전원·기준전압과 WS2812 도 이 레일이다.
     *    화면은 note 를 그대로 띄우므로(규격 §7.3), note 가 사라지면
     *    사용자가 모르고 끄게 된다. 그것을 여기서 막는다. */
    setup();
    const MkCfgItem *it = mk_cfg_find(&CFG, "pwr.5v");
    CHECK(it != NULL, "pwr.5v 항목이 있다");
    if (it != NULL) {
        CHECK(!it->interlocked && !it->readonly, "5V 를 바꿀 수 있다");
        CHECK(it->def.u == 1u, "5V 기본값은 켜짐");
        CHECK(it->note != NULL && it->note[0] != '\0',
              "끄면 무엇이 멈추는지 사유가 붙어 있다");
    }
}

static void test_all_channels_are_present(void)
{
    setup();
    int missing = 0;
    for (int ch = 0; ch < MK_AIN_COUNT; ch++) {
        char key[24];
        snprintf(key, sizeof key, "ain%d.enabled", ch);
        if (mk_cfg_find(&CFG, key) == NULL) {
            printf("       %s 없음\n", key);
            missing++;
        }
    }
    CHECK(missing == 0, "J3~J9 일곱 채널이 모두 있다");
}

/* ---- 저장 덩어리 --------------------------------------------------------- */

static void test_pack_unpack_round_trips(void)
{
    /* 🔴 포인터를 그대로 굽지 않는다는 계약(mk_cfgtable.h)을 지키는지 본다.
     *    값만 담기므로, 담았다 되돌리면 값이 그대로여야 한다. */
    /* 🔴 크기를 여기서 어림하지 않는다. `app/` 이 선언한 상한을 그대로
     *    쓴다 — 2048 로 적어 두었더니 항목이 86 개가 되면서 시험이 먼저
     *    깨졌고, 정작 확인하려던 것(값이 살아 돌아오는가)은 못 봤다. */
    static unsigned char blob[MK_CFG_BLOB_MAX];
    setup();
    CHECK(mk_cfgtable_blob_size() <= sizeof blob, "덩어리가 버퍼에 들어간다");

    /* 값을 흔들어 놓고 담는다. */
    MkCfgItem *period = mk_cfg_find(&CFG, "tx.period_ms");
    MkCfgItem *id = mk_cfg_find(&CFG, "dev.id");
    if (period != NULL) period->cur.u = 250u;
    if (id != NULL) {
        memset(id->cur.s, 0, sizeof id->cur.s);
        memcpy(id->cur.s, "abc", 3);
    }
    mk_cfgtable_pack(&CFG, blob);

    /* 표를 처음 상태로 되돌린 뒤 덩어리에서 복원한다. */
    mk_cfgtable_init(&CFG);
    mk_cfgtable_unpack(&CFG, blob);

    period = mk_cfg_find(&CFG, "tx.period_ms");
    id = mk_cfg_find(&CFG, "dev.id");
    CHECK(period != NULL && period->cur.u == 250u, "수가 살아 돌아온다");
    CHECK(id != NULL && strcmp(id->cur.s, "abc") == 0, "문자열도 살아 돌아온다");
    CHECK(CFG.items[0].key != NULL, "키 포인터는 표에 그대로 남는다");
}

/* ---- 카탈로그 덤프 ------------------------------------------------------- */

static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    fwrite(line, 1u, len, stdout);
    putchar('\n');
}

static void dump_catalog(void)
{
    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);
    setup();
    mk_cfgwire_list(&CFG, fields, n_fields, 0, emit, NULL);
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--catalog") == 0) {
        dump_catalog();
        return 0;
    }
    printf("mk_cfgtable (보드에 실리는 실제 표)\n");
    test_every_key_is_unique();
    test_every_item_has_a_label();
    test_no_pin_names_anywhere();
    test_defaults_are_inside_their_own_range();
    test_current_starts_at_default();
    test_enums_carry_choices();
    test_enum_defaults_are_choices();
    test_interlocked_items_say_why();
    test_five_volt_rail_is_settable_but_warns();
    test_all_channels_are_present();
    test_pack_unpack_round_trips();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
