#include <stdio.h>
#include "../app/mk_cfgtable.h"
#include "../app/mk_cfgwire.h"

static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    fwrite(line, 1, len, stdout);
    fputc('\n', stdout);
}

int main(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);
    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);
    mk_cfgwire_list(&cfg, fields, n_fields, 0, emit, NULL);
    return 0;
}
