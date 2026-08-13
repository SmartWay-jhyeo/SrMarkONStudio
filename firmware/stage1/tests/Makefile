# 호스트 네이티브 빌드. 크로스 툴체인도 보드도 필요 없다.
CC ?= gcc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g

all: test_framing
	./test_framing

test_framing: test_framing.c ../app/mk_framing.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f test_framing test_framing.exe
.PHONY: all clean
