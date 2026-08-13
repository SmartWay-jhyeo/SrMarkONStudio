# H723 을 리셋만 한다. 굽지 않는다. 지우지 않는다.
set confirm off
set pagination off
target extended-remote \\.\COM24
monitor swdp_scan
attach 1
info registers pc
monitor reset
detach
quit
