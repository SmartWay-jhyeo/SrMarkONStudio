# 호스트 네이티브 빌드. 크로스 툴체인도 보드도 필요 없다.
CC ?= gcc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g

TESTS = test_framing test_json test_hostlink

all: $(TESTS)
	./test_framing
	./test_json
	./test_hostlink

test_framing: test_framing.c ../app/mk_framing.c
	$(CC) $(CFLAGS) -o $@ $^

test_json: test_json.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

test_hostlink: test_hostlink.c ../app/mk_hostlink.c ../app/mk_framing.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f $(TESTS) $(addsuffix .exe,$(TESTS))
.PHONY: all clean
