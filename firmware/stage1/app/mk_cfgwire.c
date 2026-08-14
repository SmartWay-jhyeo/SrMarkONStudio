#include "mk_cfgwire.h"

#include "mk_json.h"

#include <string.h>

/* 규격 §7.3 의 vtype 문자열. */
static const char *vtype_name(MkVType t)
{
    switch (t) {
    case MK_VT_BOOL: return "bool";
    case MK_VT_U8:   return "u8";
    case MK_VT_U16:  return "u16";
    case MK_VT_U32:  return "u32";
    case MK_VT_F32:  return "f32";
    case MK_VT_STR:  return "str";
    case MK_VT_ENUM: return "enum";
    default:         return "str";
    }
}

/* 값 하나를 vtype 에 맞는 JSON 타입으로 담는다.
 *
 * 🔴 `cur` 의 JSON 타입은 항목의 vtype 을 따른다(규격 §5.2). bool 을
 *    1/0 으로 내면 호스트가 정수로 읽어 체크박스가 아니라 숫자 입력이
 *    된다 — 카탈로그만으로 화면을 만들기 때문에 타입이 곧 위젯이다. */
static void put_value(MkJson *j, const char *key, const MkCfgItem *item,
                      const MkValue *v)
{
    switch (item->vtype) {
    case MK_VT_BOOL:
        mk_json_bool(j, key, (int)v->u);
        break;
    case MK_VT_STR:
        mk_json_str(j, key, v->s);
        break;
    case MK_VT_F32:
        /* 🔴 소수 4자리로 고정한다. mk_cfg_format 과 같은 규칙이어야
         *    호스트가 되읽어 쓸 때 값이 흘러가지 않는다. */
        mk_json_f32(j, key, v->f, 4);
        break;
    default:
        mk_json_u32(j, key, v->u);
        break;
    }
}

static void common(MkJson *j, int64_t now_ms, const char *type)
{
    mk_json_u32(j, "schema_ver", 3u);
    /* 🔴 명령 응답의 seq 는 항상 0 이다(규격 §5.2). 텔레메트리 seq 와 같은
     *    계열이 아니므로 유실 검출에 쓰면 안 된다. */
    mk_json_u32(j, "seq", 0u);
    mk_json_i64(j, "t", now_ms);
    mk_json_str(j, "type", type);
}

void mk_cfgwire_list(const MkConfig *cfg,
                     const MkFieldBit *fields, size_t n_fields,
                     int64_t now_ms, MkCfgEmit emit, void *ctx)
{
    char buf[320];
    MkJson j;

    for (size_t i = 0; i < cfg->count; i++) {
        const MkCfgItem *it = &cfg->items[i];

        mk_json_begin(&j, buf, sizeof buf);
        common(&j, now_ms, "cfg_item");
        mk_json_str(&j, "key", it->key);
        mk_json_str(&j, "grp", it->group);
        mk_json_str(&j, "vtype", vtype_name(it->vtype));
        put_value(&j, "default", it, &it->def);
        put_value(&j, "cur", it, &it->cur);
        /* 🔴 `ro` 는 인터록도 포함한다. 사용자가 못 바꾸는 것은 마찬가지고,
         *    왜 못 바꾸는지는 `note` 가 말한다. 화면은 ro 로 비활성만
         *    정하고 사유는 note 를 띄운다. */
        mk_json_bool(&j, "ro", it->readonly || it->interlocked);
        if (it->label) {
            mk_json_str(&j, "label", it->label);
        }
        /* 🔴 없는 것을 0 으로 실어 보내지 않는다. 화면이 "최소 0" 이라고
         *    잘못 말한다. 순서는 시뮬레이터와 같게 둔다 — 두 카탈로그를
         *    나란히 놓고 볼 일이 많다. */
        if (it->has_min) {
            mk_json_f32(&j, "min", it->min, 0);
        }
        if (it->has_max) {
            mk_json_f32(&j, "max", it->max, 0);
        }
        if (it->unit) {
            mk_json_str(&j, "unit", it->unit);
        }
        if (it->note) {
            mk_json_str(&j, "note", it->note);
        }
        /* 🔴 enum 은 허용값 목록이 함께 와야 한다(규격 §7.3). 없으면
         *    호스트가 콤보를 만들 수 없다 — 항목을 하드코딩하지 않으므로
         *    허용값도 보드가 알려 줘야 한다. */
        if (it->n_choices > 0 && it->choices != NULL) {
            mk_json_u32_array(&j, "choices", it->choices, it->n_choices);
        }
        int n = mk_json_end(&j);
        if (n > 0 && emit) {
            emit(ctx, buf, (size_t)n);
        }
    }

    for (size_t i = 0; i < n_fields; i++) {
        mk_json_begin(&j, buf, sizeof buf);
        common(&j, now_ms, "cfg_field");
        mk_json_u32(&j, "bit", fields[i].bit);
        mk_json_str(&j, "name", fields[i].name);
        mk_json_bool(&j, "default", (int)fields[i].def);
        if (fields[i].label) {
            mk_json_str(&j, "label", fields[i].label);
        }
        int n = mk_json_end(&j);
        if (n > 0 && emit) {
            emit(ctx, buf, (size_t)n);
        }
    }

    /* 🔴 count 는 cfg_item + cfg_field 합계다(규격 §7.3). 수신측이 이것을
     *    대조해 전송이 중간에 잘렸는지 판정한다 — 링크가 나쁠 때 절반만
     *    온 카탈로그로 화면을 그리면 안 된다. */
    mk_json_begin(&j, buf, sizeof buf);
    common(&j, now_ms, "cfg_end");
    mk_json_u32(&j, "count", (uint32_t)(cfg->count + n_fields));
    int n = mk_json_end(&j);
    if (n > 0 && emit) {
        emit(ctx, buf, (size_t)n);
    }
}

/* 레일의 현재 명령 상태를 설정에서 읽는다. 없으면 꺼진 것으로 본다.
 *
 * 🔴 없는 것을 켜진 것으로 보지 않는다. 설정에서 항목이 사라지는 것은
 *    실수이고, 실수했을 때 24V 가 켜져 있다고 말하면 안 된다. */
static int rail_of(const MkConfig *cfg, const char *key)
{
    const MkCfgItem *it = mk_cfg_find((MkConfig *)cfg, key);
    return (it != NULL && it->vtype == MK_VT_BOOL && it->cur.u) ? 1 : 0;
}

int mk_cfgwire_stat(const MkConfig *cfg, int64_t now_ms,
                    const char *mode, const char *fw, const char *board_rev,
                    uint32_t uptime_ms,
                    const char *time_source, uint32_t time_quality,
                    const MkQueueStat *queues, size_t n_queues,
                    char *out, size_t cap)
{
    MkJson j;
    mk_json_begin(&j, out, cap);
    common(&j, now_ms, "stat");
    mk_json_str(&j, "mode", mode);
    mk_json_str(&j, "fw", fw);
    mk_json_str(&j, "board_rev", board_rev);
    mk_json_str(&j, "time_source", time_source);
    mk_json_u32(&j, "time_quality", time_quality);
    mk_json_u32(&j, "uptime_ms", uptime_ms);

    /* 🔴 명령 상태이지 실측이 아니다. 호스트가 `ON 명령됨` 으로 표시한다. */
    mk_json_object_begin(&j, "rails");
    mk_json_bool(&j, "v24", rail_of(cfg, "pwr.24v"));
    mk_json_bool(&j, "v14v9", rail_of(cfg, "pwr.14v9"));
    mk_json_bool(&j, "v5", rail_of(cfg, "pwr.5v"));
    mk_json_object_end(&j);

    mk_json_array_begin(&j, "queues");
    for (size_t i = 0; i < n_queues; i++) {
        mk_json_array_object_begin(&j);
        mk_json_u32(&j, "ch", queues[i].ch);
        mk_json_u32(&j, "depth", queues[i].depth);
        mk_json_u32(&j, "peak", queues[i].peak);
        mk_json_u32(&j, "drops", queues[i].drops);
        mk_json_array_object_end(&j);
    }
    mk_json_array_end(&j);

    return mk_json_end(&j);
}

int mk_cfgwire_value(const MkCfgItem *item, int64_t now_ms,
                     char *out, size_t cap)
{
    MkJson j;
    mk_json_begin(&j, out, cap);
    common(&j, now_ms, "cfg_value");
    mk_json_str(&j, "key", item->key);
    put_value(&j, "cur", item, &item->cur);
    return mk_json_end(&j);
}
