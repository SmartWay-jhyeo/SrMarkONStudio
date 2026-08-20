#include "mk_hostlink.h"

#include "mk_ads1256.h"
#include "mk_railctl.h"
#include "mk_solctl.h"
#include "mk_timeax.h"
#include "mk_gnssctl.h"
#include "mk_lcd.h"
#include "mk_linkbaud.h"
#include "mk_json.h"

#include <string.h>

static void emit_line(MkHostlink *h, const char *payload)
{
    int n = mk_build_line(h->out, sizeof h->out, payload);
    if (n > 0 && h->emit != NULL) {
        h->emit(h->ctx, h->out, (size_t)n);
    }
    /* n < 0 이면 보내지 않는다. 반쪽짜리 줄을 흘리지 않는다. */
}

static void emit_sack_ok(MkHostlink *h, const char *verb)
{
    char payload[MK_LINE_MAX + 1];
    size_t n = 0;
    const char *p;

    for (p = "SACK,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = verb;    *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = ",OK";   *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    payload[n] = '\0';
    emit_line(h, payload);
}

static void emit_sack_err(MkHostlink *h, const char *verb, const char *reason)
{
    char payload[MK_LINE_MAX + 1];
    size_t n = 0;
    const char *p;

    for (p = "SACK,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = verb;    *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = ",ERR,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = reason;  *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    payload[n] = '\0';
    emit_line(h, payload);
}

/* 장치 카운터(ms)를 규격 §7.1 의 시간축으로 옛긴다.
 *
 * 🔴 [정정, 2026-08-20] 명령 응답의 `t` 가 시간축을 안 따르고 있었다.
 *    실기기에서 텔레메트리는 `t: 1787193172927`(UTC epoch)인데 같은 순간의
 *    `$STAT` 은 `t: 947472`(부팅 후 ms)였다.
 *
 *    §7.1 은 `t` 의 뜻을 `time_source` **하나로** 정한다 — 레코드 종류로
 *    나누지 않는다. 그리고 가동 시간은 이미 `uptime_ms` 로 따로 실리므로,
 *    같은 사실이 두 자리에 있으면서 그중 한 자리는 이름이 거짓말을 하고
 *    있었던 셈이다. 호스트가 명령 응답의 `t` 를 시각으로 쓸 일이 없어
 *    드러나지 않았을 뿐, 한 스트림 안에서 같은 이름의 필드가 줄마다 다른
 *    시간축을 쓰는 것은 저장·정렬에서 조용히 터질 자리다.
 *
 *    timeax 가 안 붙어 있으면(1단계 빌드) 변환 없이 그대로 — mk_telem.c 의
 *    acquired_epoch_ms() 와 같은 규칙·같은 단위 변환(ms -> us)이다. */
static int64_t axis_ms(MkHostlink *h, int64_t now_ms)
{
    if (h->timeax == NULL) {
        return now_ms;
    }
    return mk_timeax_now_ms_monotonic(h->timeax, (uint64_t)now_ms * 1000ULL);
}

/* 규격 §5.2 — `$ID` 는 id 레코드 한 줄 뒤에 $SACK 를 보낸다. */
/* 반환: 내보냈으면 1, 못 내보냈으면 0.
 *
 * 🔴 호출 쪽이 이 값을 봐야 한다. 레코드를 못 만들었는데 $SACK,ID,OK 를
 *    보내면 호스트는 데이터를 받았다고 믿는다. 그것이 아무 응답도 없는
 *    것보다 나쁘다 — 무응답은 타임아웃으로 드러나지만, 거짓 OK 는
 *    드러나지 않는다. */
static int emit_id_record(MkHostlink *h, int64_t now_ms)
{
    char body[MK_LINE_MAX + 8];
    MkJson j;

    mk_json_begin(&j, body, sizeof body);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);          /* §5.2 — 명령 응답의 seq 는 항상 0 */
    mk_json_i64(&j, "t", axis_ms(h, now_ms));
    mk_json_str(&j, "type", "id");
    mk_json_str(&j, "device_id", h->device_id);
    mk_json_str(&j, "fw", h->fw);
    mk_json_str(&j, "board_rev", h->board_rev);

    int n = mk_json_end(&j);
    if (n <= 0 || h->emit == NULL) {
        return 0;                        /* 잘린 JSON 은 내보내지 않는다 */
    }
    /* NDJSON 은 줄바꿈으로 끝난다. 버퍼에 자리가 있는지 먼저 본다.
     * 🔴 `n + 2` 는 줄바꿈과 NUL 이다. 이 검사가 없으면 n 이 버퍼 끝에
     *    닿았을 때 body[n], body[n+1] 이 범위를 벗어난다. */
    if ((size_t)n + 2u > sizeof body) {
        return 0;
    }
    body[n]     = '\n';
    body[n + 1] = '\0';
    h->emit(h->ctx, body, (size_t)n + 1u);
    return 1;
}

/* NDJSON 한 줄을 내보낸다. 줄바꿈을 붙일 자리가 없으면 보내지 않는다. */
static int emit_json(MkHostlink *h, char *body, size_t cap, int n)
{
    if (n <= 0 || h->emit == NULL) {
        return 0;
    }
    if ((size_t)n + 2u > cap) {
        return 0;
    }
    body[n]     = '\n';
    body[n + 1] = '\0';
    h->emit(h->ctx, body, (size_t)n + 1u);
    return 1;
}

/* mk_cfgwire 가 카탈로그 한 줄을 만들 때마다 부른다. */
static void catalog_sink(void *ctx, const char *line, size_t len)
{
    MkHostlink *h = (MkHostlink *)ctx;
    char body[400];

    if (h->emit == NULL || len + 2u > sizeof body) {
        return;
    }
    memcpy(body, line, len);
    body[len]     = '\n';
    body[len + 1] = '\0';
    h->emit(h->ctx, body, len + 1u);
}

/* MkCfgResult 를 SACK 사유로. */
static void sack_cfg(MkHostlink *h, MkCfgResult r)
{
    if (r == MK_CFG_OK) {
        emit_sack_ok(h, "CFG");
    } else {
        emit_sack_err(h, "CFG", mk_cfg_reason_text(r));
    }
}

static void on_cfg(MkHostlink *h, const MkCommand *c, int64_t now_ms)
{
    if (h->cfg == NULL) {
        emit_sack_err(h, "CFG", "UNSUPPORTED");
        return;
    }
    if (c->argc < 1) {
        emit_sack_err(h, "CFG", "UNKNOWN_KEY");
        return;
    }

    const char *sub = c->args[0];

    if (strcmp(sub, "LIST") == 0) {
        /* 규격 §5.2 — 본문을 먼저 보내고 $SACK 로 끝낸다. */
        mk_cfgwire_list(h->cfg, h->fields, h->n_fields, axis_ms(h, now_ms),
                        catalog_sink, h);
        emit_sack_ok(h, "CFG");
        return;
    }

    if (strcmp(sub, "GET") == 0) {
        if (c->argc < 2) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        MkCfgItem *item = mk_cfg_find(h->cfg, c->args[1]);
        if (item == NULL) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        char body[256];
        int n = mk_cfgwire_value(item, axis_ms(h, now_ms), body, sizeof body);
        /* 🔴 본문을 못 만들었으면 OK 라고 하지 않는다. 거짓 OK 는 무응답보다
         *    나쁘다 — 호스트가 값을 받았다고 믿고 넘어간다. */
        if (!emit_json(h, body, sizeof body, n)) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        emit_sack_ok(h, "CFG");
        return;
    }

    /* 아래는 전부 CONFIG 전용이다 (규격 §4).
     *
     * 🔴 모드 검사를 키 존재보다 **먼저** 한다. RUN 모드에서 온 요청은
     *    키가 맞든 틀리든 받지 않으므로, 키를 먼저 보면 없는 키에 대해
     *    UNKNOWN_KEY 를 돌려주게 된다 — 사용자는 키 이름을 고치려 들지만
     *    진짜 문제는 GUI 가 하트비트를 못 보내고 있다는 것이다. */
    if (mk_hostlink_mode(h, now_ms) != MK_MODE_CONFIG) {
        emit_sack_err(h, "CFG", "MODE");
        return;
    }

    if (strcmp(sub, "SET") == 0) {
        if (c->argc < 3) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        sack_cfg(h, mk_cfg_set(h->cfg, c->args[1], c->args[2]));
        return;
    }

    if (strcmp(sub, "SAVE") == 0) {
        if (h->save == NULL) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        /* 🔴 확정되지 않은 링크 속도는 Flash 에 가지 않는다
         *    (규격 §4.2.2 규칙 5).
         *
         *    이 규칙이 없으면 "부팅하자마자 아무도 말을 못 거는 보드" 가
         *    만들어진다. 다른 잘못된 설정은 다시 고칠 수 있지만 그것은
         *    못 고친다 — **굽기로만 풀린다.** 그리고 이 저장소에서 굽기는
         *    하루에 네 번 막힌 적이 있고 매번 보드 전원을 20초 빼야
         *    풀렸다(CLAUDE.md §4).
         *
         *    저장을 미루는 것뿐이라 잃는 것이 없다 — 호스트는 확인을 마친
         *    뒤 다시 저장하면 된다. */
        if (h->linkbaud != NULL && mk_linkbaud_is_pending(h->linkbaud)) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        /* 🔴 바뀐 것이 없으면 쓰지 않는다. Flash 를 지웠다 쓰는 것은
         *    수명을 깎는 일이고, 아무것도 안 바뀐 저장은 순수한 손해다.
         *    사용자에게는 성공으로 보인다 — 실제로 저장된 상태와 같으므로
         *    거짓말이 아니다. */
        if (!mk_cfg_dirty(h->cfg)) {
            emit_sack_ok(h, "CFG");
            return;
        }
        /* 🔴 TEST 에서는 출력 항목을 기본값으로 바꿔 놓고 저장한다
         *    (규격 §6.4). 벤치에서 배선을 보려고 밸브를 한 번 열어 본 것이
         *    Flash 에 남아 다음 부팅에 되살아나면 안 된다. 저장이 끝나면
         *    화면에 떠 있는 값으로 되돌려 놓는다 — 테스트는 계속된다. */
        MkValue backup[MK_CFG_MAX_OUT];
        size_t stashed = 0;
        if (h->ctl_mode == MK_CTL_TEST) {
            stashed = mk_cfg_outputs_stash(h->cfg, backup, MK_CFG_MAX_OUT);
        }
        int rc = h->save(h->save_ctx);
        if (stashed) {
            mk_cfg_outputs_unstash(h->cfg, backup, stashed);
        }
        if (rc != 0) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        mk_cfg_mark_saved(h->cfg);
        emit_sack_ok(h, "CFG");
        return;
    }

    if (strcmp(sub, "RESET") == 0) {
        mk_cfg_reset(h->cfg);
        emit_sack_ok(h, "CFG");
        return;
    }

    emit_sack_err(h, "CFG", "UNSUPPORTED");
}

/* `$MODE,<TEST|ACTIVE>` — 제어 모드 전환 (규격 §6.4). */
static void on_mode(MkHostlink *h, const MkCommand *c, int64_t now_ms)
{
    if (c->argc < 1) {
        emit_sack_err(h, "MODE", "RANGE");
        return;
    }

    MkCtlMode want;
    if (strcmp(c->args[0], "TEST") == 0) {
        want = MK_CTL_TEST;
    } else if (strcmp(c->args[0], "ACTIVE") == 0) {
        want = MK_CTL_ACTIVE;
    } else {
        emit_sack_err(h, "MODE", "RANGE");
        return;
    }

    /* 🔴 TEST 는 CONFIG 안에서만 산다. TEST 의 안전장치가 하트비트
     *    데드맨이므로, 하트비트 없이 들어가면 그 장치가 없는 상태가 된다. */
    if (want == MK_CTL_TEST && mk_hostlink_mode(h, now_ms) != MK_MODE_CONFIG) {
        emit_sack_err(h, "MODE", "MODE");
        return;
    }

    if (want != h->ctl_mode) {
        if (h->ctl_mode == MK_CTL_TEST && h->cfg != NULL) {
            mk_cfg_outputs_to_default(h->cfg);
        }
        h->ctl_mode = want;
    }
    emit_sack_ok(h, "MODE");
}

/* `$GNSS,<text>` — GNSS 모듈에 원시 명령 전달(규격 §4.1).
 *
 * 🔴 모드 검사가 먼저다 — $CFG,SET 과 같은 이유(on_cfg 위 주석). RUN 에서
 *    온 요청은 내용이 맞든 틀리든 받지 않는다. */
static int is_gnss_text_char(char c)
{
    unsigned char u = (unsigned char)c;
    return u >= 0x20u && u <= 0x7Eu;    /* 인쇄 가능 ASCII 만(규격 §4.1) */
}

static void on_gnss(MkHostlink *h, const MkCommand *c, int64_t now_ms)
{
    if (mk_hostlink_mode(h, now_ms) != MK_MODE_CONFIG) {
        emit_sack_err(h, "GNSS", "MODE");
        return;
    }

    /* 🔴 텍스트가 있어야 한다(argc==1). mk_parse_line 이 이미 $GNSS 를
     *    원문 꼬리(raw tail)로 파싱해 뒀다 — 쉼표로 다시 쪼개지 않는다
     *    (mk_framing.h 의 MK_GNSS_TEXT_MAX 주석). 빈 문자열도 거부한다
     *    (규격 §4.1). */
    if (c->argc != 1 || c->gnss_text[0] == '\0') {
        emit_sack_err(h, "GNSS", "RANGE");
        return;
    }
    for (const char *p = c->gnss_text; *p != '\0'; p++) {
        if (!is_gnss_text_char(*p)) {
            emit_sack_err(h, "GNSS", "RANGE");
            return;
        }
    }

    if (h->gnss_send == NULL) {
        emit_sack_err(h, "GNSS", "UNSUPPORTED");
        return;
    }

    /* 🔴 줄 끝 CR/LF 는 여기서 붙인다 — bsp 의 send 콜백은 받은 바이트를
     *    그대로 내보내기만 하는 얇은 관이다(mk_gnss.h 의 MkGnssSend 주석).
     *    그래야 이 로직이 호스트에서 시험된다. 텍스트는 MK_GNSS_TEXT_MAX
     *    (96) 바이트 이하이므로 +2(CR/LF)가 이 버퍼를 넘지 않는다. */
    char buf[MK_GNSS_TEXT_MAX + 2];
    size_t tlen = 0;
    while (c->gnss_text[tlen] != '\0') {
        buf[tlen] = c->gnss_text[tlen];
        tlen++;
    }
    buf[tlen]     = '\r';
    buf[tlen + 1] = '\n';

    if (!h->gnss_send(h->gnss_send_ctx, buf, tlen + 2u)) {
        emit_sack_err(h, "GNSS", "BUSY");
        return;
    }
    emit_sack_ok(h, "GNSS");
}

/* 규격 §7.4 — `$STAT` 은 stat 레코드 한 줄 뒤에 $SACK 를 보낸다.
 *
 * 🔴 CONFIG 전용이 아니다 (규격 §4). RUN 에서 상태를 못 보면 진단할 수
 *    없다 — 유실이 나는 것은 대개 RUN 일 때다. */
void mk_hostlink_attach_ads(MkHostlink *h, struct MkAds *ads)
{
    h->ads = ads;
}

void mk_hostlink_attach_rails(MkHostlink *h, struct MkRailCtl *rails)
{
    h->rails = rails;
}

void mk_hostlink_attach_sol(MkHostlink *h, struct MkSolCtl *sol)
{
    h->sol = sol;
}

void mk_hostlink_attach_timeax(MkHostlink *h, struct MkTimeAx *timeax)
{
    h->timeax = timeax;
}

void mk_hostlink_attach_gnss(MkHostlink *h, MkGnssSend send, void *ctx)
{
    h->gnss_send = send;
    h->gnss_send_ctx = ctx;
}

void mk_hostlink_attach_gnssctl(MkHostlink *h, struct MkGnssCtl *gnssctl)
{
    h->gnssctl = gnssctl;
}

void mk_hostlink_attach_lcd(MkHostlink *h, struct MkLcd *lcd)
{
    h->lcd = lcd;
}

void mk_hostlink_attach_clock(MkHostlink *h, const char *src,
                              uint32_t sysclk_hz)
{
    h->clock_src = src;
    h->clock_sysclk_hz = sysclk_hz;
}

void mk_hostlink_attach_linkbaud(MkHostlink *h, struct MkLinkBaud *lb)
{
    h->linkbaud = lb;
}

/* `$BAUD,CONFIRM,<baud>` — 링크 속도 확인 (규격 §4.2.3).
 *
 * 🔴 **CONFIG 전용이 아니다.** 호스트는 포트를 닫았다 새 속도로 다시 여는
 *    동안 $HB 를 못 보내고, 그것이 3000 ms 를 넘으면 보드는 RUN 으로
 *    떨어진다(규격 §6.2). CONFIG 전용으로 두면 **포트를 여는 데 오래
 *    걸린 것만으로 확인이 거부되고, 그 거부가 곧 우리가 막으려던 되돌림을
 *    부른다.** 그리고 이 명령은 설정을 바꾸지 않는다 — CONFIG 에서 이미
 *    허가된 변경을 유지할 뿐이다. */
static void on_baud(MkHostlink *h, const MkCommand *c)
{
    if (h->linkbaud == NULL) {
        emit_sack_err(h, "BAUD", "UNSUPPORTED");
        return;
    }
    if (c->argc < 1 || strcmp(c->args[0], "CONFIRM") != 0) {
        emit_sack_err(h, "BAUD", "UNSUPPORTED");
        return;
    }
    if (c->argc < 2) {
        emit_sack_err(h, "BAUD", "RANGE");
        return;
    }

    /* 🔴 값을 함께 받는 이유(규격 §4.2.3): 이 명령은 **새 속도로** 와야
     *    하므로, 값이 되돌아온다는 것 자체가 "호스트가 무엇을 확인하는지
     *    알고 그 속도로 말할 수 있다" 는 증거다. */
    uint32_t baud = 0u;
    const char *p = c->args[1];
    if (*p == '\0') {
        emit_sack_err(h, "BAUD", "RANGE");
        return;
    }
    for (; *p != '\0'; p++) {
        if (*p < '0' || *p > '9' || baud > 429496729u) {
            emit_sack_err(h, "BAUD", "RANGE");
            return;
        }
        baud = baud * 10u + (uint32_t)(*p - '0');
    }

    switch (mk_linkbaud_confirm(h->linkbaud, baud)) {
    case MK_LINKBAUD_OK:
        emit_sack_ok(h, "BAUD");
        break;
    case MK_LINKBAUD_ERR_RANGE:
        emit_sack_err(h, "BAUD", "RANGE");
        break;
    default:
        /* 확인할 것이 없다. 규격 §5 의 MODE — "지금 그 명령을 받을 수
         * 있는 상태가 아니다". */
        emit_sack_err(h, "BAUD", "MODE");
        break;
    }
}

static void on_stat(MkHostlink *h, int64_t now_ms)
{
    if (h->cfg == NULL) {
        emit_sack_err(h, "STAT", "UNSUPPORTED");
        return;
    }

    /* 🔴 수집기가 안 붙어 있으면 빈 배열이다. 0 을 채워 보내지 않는다 —
     *    "채널이 없다" 와 "채널이 있는데 유실이 0" 은 다른 말이고, 유실을
     *    찾으려고 이 창구를 보는 사람에게는 그 차이가 전부다.
     *
     * 🔴 켜진 채널만 싣는다. 그래서 `ch` 는 배열 첨자가 아니라 채널 번호다
     *    (규격 §7.4). 시뮬레이터도 같은 규칙이다. */
    MkQueueStat qs[MK_ADS_CHANNELS];
    size_t n_q = 0;
    if (h->ads != NULL) {
        for (int i = 0; i < MK_ADS_CHANNELS; i++) {
            MkQueue *q = mk_ads_queue(h->ads, i);
            if (q == NULL || !mk_ads_channel_enabled(h->ads, i)) {
                continue;
            }
            qs[n_q].ch = (uint8_t)i;
            qs[n_q].depth = mk_queue_count(q);
            qs[n_q].peak = mk_queue_peak(q);
            qs[n_q].drops = mk_queue_drops(q);
            n_q++;
        }
    }

    /* 🔴 7채널을 다 켰을 때의 최악 길이를 세어 잡은 값이다.
     *
     *      머리(schema_ver..uptime_ms, 최악값)      178
     *      rails                                     47
     *      din 3개(connector_id·state 고정)          97
     *      queues 7개 (ch/depth/peak/drops 최댓값)   397
     *      + 줄바꿈 + NUL                              2
     *      ---------------------------------------------
     *                                               721
     *
     *    처음에 400 으로 잡아 두었다가 되돌림 검사에서 발견했다. 꺼진 채널을
     *    거르는 코드를 지웠더니 $STAT 이 ERR,BUSY 로 떨어졌는데, 그것은
     *    **7채널을 다 켜면 정상 동작에서도 그렇게 된다**는 뜻이었다.
     *    사용자가 채널을 다 켜는 순간 진단 창구가 닫히는 셈이다.
     *
     *    din 은 항상 3개 고정(J18~J20)이라 채널 수와 무관하게 더해진다.
     *    버퍼를 768 로 두면 여유가 47 바이트뿐이라 넉넉하지 않다 — 896 으로
     *    올려 둔다.
     *
     *    이 줄은 $ 프레임이 아니라 NDJSON 이라 MK_LINE_MAX(192)의 제약을
     *    받지 않는다. 그쪽은 명령·SACK 의 payload 한도다(규격 §3). */
    /* 🔴 설정표가 아니라 레일 제어기에서 읽는다. 설정은 "사용자가 원하는
     *    것" 이고 이것은 "보드가 낸 것" 이다 — 그 차이가 순차 기동 중에
     *    드러난다. 예전에는 mk_cfgwire_stat 이 설정표를 읽어서, 부팅 직후
     *    pwr.5v 기본값(true)이 그대로 나가 핀은 0 인데 5V ON 이라고 했다
     *    (실기기 확인 2026-08-14). */
    MkRailState rs = {0, 0, 0};
    if (h->rails != NULL) {
        rs.v24   = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_24V);
        rs.v14v9 = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_14V9);
        rs.v5    = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_5V);
    }

    /* 🔴 din 은 rails 와 반대로 실측이다(규격 §7.4·§7.6) — 설정표가 아니라
     *    mk_solctl 이 EXTI 로 읽은 값을 그대로 싣는다. 안 붙어 있으면
     *    빈 배열이다. */
    MkDinState ds[MK_SOL_COUNT];
    size_t n_din = 0;
    if (h->sol != NULL) {
        for (int c = 0; c < MK_SOL_COUNT; c++) {
            ds[n_din].connector_id = (uint16_t)mk_sol_connector_of((MkSolCh)c);
            ds[n_din].state        = (uint8_t)mk_solctl_is_on(h->sol, (MkSolCh)c);
            n_din++;
        }
    }

    /* 🔴 Phase 3 — timeax 가 붙어 있으면 실제 등급을 싣는다. 안 붙어
     *    있으면(1단계와 같은 빌드 경로) "device_clock" 고정이다 — `t` 는
     *    부팅 후 경과 ms 이고 UTC 가 아니다. */
    const char *time_source = "device_clock";
    uint32_t    time_quality = 0u;
    int64_t     gnss_pps_age_ms = -1;
    int64_t     gnss_pps_raw_age_ms = -1;
    uint32_t    gnss_pps_raw_count = 0u;
    const char *gnss_pps_unpaired_reason = NULL;
    int32_t     gnss_sats = -1;
    if (h->timeax != NULL) {
        time_source  = mk_timeax_grade_name(mk_timeax_grade(h->timeax));
        time_quality = mk_timeax_time_quality(h->timeax);
        int64_t age_us = mk_timeax_pps_age_us_now(h->timeax);
        gnss_pps_age_ms = (age_us >= 0) ? age_us / 1000 : -1;
        gnss_sats = mk_timeax_sats(h->timeax);   /* GGA 가 한 번도 없어도 0 — sats 는
                                                    * "본 적 없음" 을 따로 구분할 근거가
                                                    * 없다(uint8_t 0 이 곧 그 뜻이다). */

        /* 🔴 원시 캡처 — "펄스가 오는가"를 짝짓기와 무관하게 답한다(규격
         *    §7.4, 실기기 관측 근거는 mk_timeax.h 헤더 주석). */
        int64_t raw_age_us = mk_timeax_pps_raw_age_us_now(h->timeax);
        gnss_pps_raw_age_ms = (raw_age_us >= 0) ? raw_age_us / 1000 : -1;
        gnss_pps_raw_count = mk_timeax_pps_raw_count(h->timeax);
        gnss_pps_unpaired_reason =
            mk_timeax_pps_unpaired_reason_name(mk_timeax_pps_unpaired_reason(h->timeax));
    }

    /* 🔴 GNSS 초기화 진단(규격 §4.1.1·§7.4). gnssctl 이 안 붙어 있으면
     *    전부 거짓이다 — timeax·rails·sol 이 안 붙었을 때와 같은 결. */
    int gnss_init_sent = 0;
    int gnss_init_exhausted = 0;
    int gnss_sentence_seen = 0;
    if (h->gnssctl != NULL) {
        gnss_init_sent      = mk_gnssctl_sent(h->gnssctl);
        gnss_init_exhausted = mk_gnssctl_exhausted(h->gnssctl);
        gnss_sentence_seen  = mk_gnssctl_sentence_seen(h->gnssctl);
    }

    /* 🔴 화면 회복 계수기(규격 §7.4). 안 붙어 있으면 NULL 을 넘겨
     *    전부 0 · readback = null 로 나간다 — timeax·rails·sol 과 같은 결. */
    MkLcdStat ls;
    if (h->lcd != NULL) {
        mk_lcd_stat(h->lcd, &ls);
    }

    /* 🔴 호스트 링크 속도(규격 §4.2·§7.4). 안 붙어 있으면 NULL 을 넘겨
     *    `baud`·`confirmed` 가 null 로 나간다 — clock 과 같은 결로,
     *    속도를 지어내지 않는다. */
    MkLinkStat lks;
    if (h->linkbaud != NULL) {
        lks.baud            = mk_linkbaud_active(h->linkbaud);
        lks.confirmed       = mk_linkbaud_confirmed(h->linkbaud);
        lks.pending         = mk_linkbaud_pending(h->linkbaud);
        lks.remaining_ms    = mk_linkbaud_remaining_ms(h->linkbaud, now_ms);
        lks.applied         = h->linkbaud->applied;
        lks.confirmed_count = h->linkbaud->confirmed_count;
        lks.reverted        = h->linkbaud->reverted;
    }

    /* 🔴 896 이던 것을 1024 로, 그 뒤 gnss.init_* 세 필드로 더 올렸다.
     *    [신규, 2026-08-19] "pps_raw_age_ms":<i64>,"pps_raw_count":<u32>,
     *    "pps_unpaired_reason":"no_valid_nmea" 가 최악 ~95바이트를 더
     *    먹는다(문자열 이유 중 가장 긴 "no_valid_nmea" 기준). 옛 여유가
     *    빠듯해 1280 으로 올려 다시 확보한다.
     *
     *    [신규, 2026-08-19] `lcd` 객체가 최악 ~120바이트를 더 먹는다
     *    (계수기 여섯이 각각 10자리까지 갈 수 있다). 1408 로 올린다.
     *
     *    [신규, 2026-08-19] `clock` 객체가 최악 ~48바이트를 더 먹는다
     *    ("clock":{"src":"hse_pll","sysclk_hz":4294967295}). 1472 로
     *    올린다 — 이 버퍼가 모자라면 $STAT 이 ERR,BUSY 로 떨어져
     *    **진단 창구가 통째로 닫힌다**(이 파일 위 실기기 기록).
     *
     *    [신규, 2026-08-20] `link` 객체가 최악 ~175바이트를 더 먹는다
     *    (u32 넷이 각각 10자리 + remaining_ms 가 i64). 1664 로 올린다. */
    char body[1664];
    int n = mk_cfgwire_stat(
        axis_ms(h, now_ms),
        mk_hostlink_mode(h, now_ms) == MK_MODE_CONFIG ? "CONFIG" : "RUN",
        h->ctl_mode == MK_CTL_TEST ? "TEST" : "ACTIVE",
        h->fw, h->board_rev, (uint32_t)now_ms,
        h->clock_src, h->clock_sysclk_hz,
        time_source, time_quality,
        gnss_pps_age_ms,
        gnss_pps_raw_age_ms, gnss_pps_raw_count, gnss_pps_unpaired_reason,
        gnss_sats,
        gnss_init_sent, gnss_init_exhausted, gnss_sentence_seen,
        &rs,
        n_din > 0 ? ds : NULL, n_din,
        n_q > 0 ? qs : NULL, n_q,
        h->linkbaud != NULL ? &lks : NULL,
        h->lcd != NULL ? &ls : NULL,
        body, sizeof body);

    if (!emit_json(h, body, sizeof body, n)) {
        emit_sack_err(h, "STAT", "BUSY");
        return;
    }
    emit_sack_ok(h, "STAT");
}

void mk_hostlink_attach_config(MkHostlink *h, MkConfig *cfg,
                               const MkFieldBit *fields, size_t n_fields,
                               MkCfgSave save, void *save_ctx)
{
    h->cfg = cfg;
    h->fields = fields;
    h->n_fields = n_fields;
    h->save = save;
    h->save_ctx = save_ctx;
}

void mk_hostlink_init(MkHostlink *h, MkEmit emit, void *ctx,
                      const char *device_id, const char *fw,
                      const char *board_rev)
{
    memset(h, 0, sizeof *h);
    h->emit = emit;
    h->ctx = ctx;
    h->device_id = device_id;
    h->fw = fw;
    h->board_rev = board_rev;
    h->last_hb_rx_ms = 0;
    h->last_hb_tx_ms = 0;
    h->hb_seen = 0;                      /* 부팅 직후는 RUN (§6.2) */
}

void mk_hostlink_feed(MkHostlink *h, const char *line, size_t len,
                      int64_t now_ms)
{
    MkCommand c;
    MkParseResult r = mk_parse_line(line, len, &c);

    if (r == MK_ERR_MALFORMED) {
        return;                          /* verb 를 못 읽었다 — 조용히 버린다 */
    }

    if (r == MK_ERR_CHECKSUM) {
        /* 🔴 $HB 는 예외다 (§3, §6.1). 체크섬이 틀려도 SACK 를 보내지
         *    않는다. 1 Hz 로 오므로 링크가 나빠지면 초당 하나씩 쌓여
         *    이미 나쁜 링크를 더 나쁘게 만든다. 알릴 내용은 어차피
         *    3초 뒤 RUN 전환으로 전달된다.
         *
         *    그리고 여기서 하트비트 시각을 갱신하지 **않는다**. 이것이
         *    §6.3 이 회귀 시험을 필수로 둔 바로 그 지점이다. */
        if (strcmp(c.verb, "HB") != 0) {
            emit_sack_err(h, c.verb, "CHECKSUM");
        }
        return;
    }

    /* 여기부터는 체크섬을 통과한 줄이다. */
    if (strcmp(c.verb, "HB") == 0) {
        /* §6.1 — 어느 방향이든 $HB 에는 $SACK 를 보내지 않는다. */
        h->last_hb_rx_ms = now_ms;
        h->hb_seen = 1;
        return;
    }

    if (strcmp(c.verb, "ID") == 0) {
        /* 레코드를 못 내보냈으면 OK 라고 하지 않는다. 거짓 OK 는 무응답보다
         * 나쁘다 — 호스트가 데이터를 받았다고 믿고 다음으로 넘어간다. */
        if (emit_id_record(h, now_ms)) {
            emit_sack_ok(h, "ID");
        }
        return;
    }

    if (strcmp(c.verb, "CFG") == 0) {
        on_cfg(h, &c, now_ms);
        return;
    }

    if (strcmp(c.verb, "STAT") == 0) {
        on_stat(h, now_ms);
        return;
    }

    if (strcmp(c.verb, "MODE") == 0) {
        on_mode(h, &c, now_ms);
        return;
    }

    if (strcmp(c.verb, "GNSS") == 0) {
        on_gnss(h, &c, now_ms);
        return;
    }

    if (strcmp(c.verb, "BAUD") == 0) {
        on_baud(h, &c);
        return;
    }

    /* 규격 §5 — 알아들었으나 이 펌웨어가 아직 구현하지 않은 명령.
     * 조용히 버리면 죽은 링크와 구분되지 않는다. */
    emit_sack_err(h, c.verb, "UNSUPPORTED");
}

void mk_hostlink_tick(MkHostlink *h, int64_t now_ms)
{
    /* 🔴 호스트가 사라지면 테스트도 끝난다 (규격 §6.4).
     *
     *    하트비트가 이미 데드맨이므로 방아쇠를 새로 만들지 않는다. 그리고
     *    그것이 옳은 방아쇠다 — 하트비트는 케이블이 꽂혔는지가 아니라
     *    저쪽에서 사람이 보고 있는지를 알려 준다. 사람이 안 보면 테스트
     *    출력은 꺼져야 한다. */
    /* 🔴 전이를 따로 기억하지 않는다. TEST 는 CONFIG 에서만 들어갈 수
     *    있으므로 "TEST 인데 RUN" 자체가 이미 호스트를 잃었다는 뜻이다.
     *    직전 모드를 들고 비교하면 그 변수를 갱신하는 첫 tick 이 언제
     *    오느냐에 답이 달라진다 — 실제로 시험이 그것을 잡았다. */
    if (h->ctl_mode == MK_CTL_TEST
            && mk_hostlink_mode(h, now_ms) != MK_MODE_CONFIG) {
        if (h->cfg != NULL) {
            mk_cfg_outputs_to_default(h->cfg);
        }
        h->ctl_mode = MK_CTL_ACTIVE;
    }

    /* 시뮬레이터(device_sim.tick)와 같은 조건이다 — `>=`. 부팅 직후
     * (now_ms 0, last_hb_tx_ms 0)에는 내보내지 않고 1000 ms 부터 시작한다. */
    if (now_ms - h->last_hb_tx_ms >= MK_HB_INTERVAL_MS) {
        h->last_hb_tx_ms = now_ms;
        emit_line(h, "HB");
    }
}

MkMode mk_hostlink_mode(const MkHostlink *h, int64_t now_ms)
{
    if (!h->hb_seen) {
        return MK_MODE_RUN;              /* 부팅 직후 기본값 */
    }
    /* 🔴 `>` 이지 `>=` 가 아니다. 정확히 3000 ms 가 지난 순간은 아직
     *    CONFIG 다. 시뮬레이터(device_sim.mode)와 같은 경계를 쓴다 —
     *    한쪽이 CONFIG 라고 보는 순간에 다른 쪽이 RUN 이면 설정 변경이
     *    간헐적으로 거부되고, 그 원인은 재현되지 않는다. */
    if (now_ms - h->last_hb_rx_ms > MK_HB_TIMEOUT_MS) {
        return MK_MODE_RUN;
    }
    return MK_MODE_CONFIG;
}

MkCtlMode mk_hostlink_ctl_mode(const MkHostlink *h)
{
    return h->ctl_mode;
}
