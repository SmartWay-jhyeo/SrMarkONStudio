# 🔴 이 개발 호스트에는 gcc 도 clang 도 없다 (2026-08-13 확인). arm-none-eabi-gcc
#    는 크로스 전용이라 호스트 시험에 못 쓴다. 있는 것은 MSVC 뿐이다.
#    Makefile 은 gcc 가 있는 환경(CI·리눅스)을 위해 남겨 둔다.
#
#    /utf-8 이 필수다. 이 파일들은 UTF-8 인데 MSVC 는 기본으로 CP949 로 읽어,
#    한글 바이트열 안의 0x5C 를 백슬래시로 오인하고 문자열을 깨뜨린다.
#
# 🔴 이 파일은 BOM 이 붙은 UTF-8 로 저장해야 한다. Windows PowerShell 5.1 은
#    BOM 이 없으면 스크립트를 CP949 로 읽어 한글이 깨지고, 아래 throw 문의
#    따옴표가 어긋나면 구문 오류로 죽는다. MSVC 문제의 거울상이다.
$ErrorActionPreference = "Stop"
$vcvars = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat 없음: $vcvars" }
Set-Location $PSScriptRoot

# 시험 묶음: 실행 파일 이름 -> 소스들
$suites = @(
    @{ exe = "test_framing.exe";  src = "test_framing.c ..\app\mk_framing.c" },
    @{ exe = "test_json.exe";     src = "test_json.c ..\app\mk_json.c" },
    @{ exe = "test_hostlink.exe"; src = "test_hostlink.c ..\app\mk_hostlink.c ..\app\mk_framing.c ..\app\mk_json.c ..\app\mk_config.c ..\app\mk_cfgwire.c ..\app\mk_ads1256.c ..\app\mk_queue.c ..\app\mk_railctl.c" },
    @{ exe = "test_config.exe";   src = "test_config.c ..\app\mk_config.c" },
    @{ exe = "test_cfgwire.exe";  src = "test_cfgwire.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c" },
    @{ exe = "test_crc.exe";      src = "test_crc.c ..\app\mk_crc.c" },
    @{ exe = "test_queue.exe";    src = "test_queue.c ..\app\mk_queue.c" },
    @{ exe = "test_ads1256.exe"; src = "test_ads1256.c ..\app\mk_ads1256.c ..\app\mk_queue.c" },
    @{ exe = "test_railctl.exe";  src = "test_railctl.c ..\app\mk_railctl.c" },
    @{ exe = "test_cfgtable.exe"; src = "test_cfgtable.c ..\app\mk_cfgtable.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c ..\app\mk_i2c.c ..\app\mk_i2c_drivers.c ..\app\mk_i2c_bh1750.c" },
    @{ exe = "test_telem.exe";    src = "test_telem.c ..\app\mk_telem.c ..\app\mk_cfgtable.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c ..\app\mk_ads1256.c ..\app\mk_queue.c ..\app\mk_i2c.c ..\app\mk_i2c_drivers.c ..\app\mk_i2c_bh1750.c" },
    @{ exe = "test_ws2812.exe";   src = "test_ws2812.c ..\app\mk_ws2812.c" },
    @{ exe = "test_sol.exe";      src = "test_sol.c ..\app\mk_solctl.c ..\app\mk_cfgtable.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c" },
    @{ exe = "test_i2c.exe";      src = "test_i2c.c ..\app\mk_i2c.c ..\app\mk_i2c_drivers.c ..\app\mk_i2c_bh1750.c ..\app\mk_cfgtable.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c ..\app\mk_ws2812.c ..\app\mk_solctl.c" }
)

# 빌드와 실행을 나눈다. 한 사슬로 묶으면 컴파일 실패와 시험 실패가 같은
# 종료 코드로 나와, 무엇이 깨졌는지 종료 코드만 보고는 알 수 없다.
foreach ($s in $suites) {
    $build = "chcp 65001 >nul && `"$vcvars`" >nul 2>&1 && " +
             "cl /nologo /W4 /WX /utf-8 /std:c11 /Fe:$($s.exe) $($s.src) >nul"
    cmd /c $build
    if ($LASTEXITCODE -ne 0) {
        Write-Output "빌드 실패: $($s.exe) (exit $LASTEXITCODE) — 시험을 돌리지 않는다."
        exit 2
    }
}

$failed = 0
foreach ($s in $suites) {
    cmd /c "chcp 65001 >nul && .\$($s.exe)"
    if ($LASTEXITCODE -ne 0) { $failed = 1 }
    Write-Output ""
}

# 🔴 C 와 Python 시뮬레이터 대조. 여기서 돌리지 않으면 아무도 돌리지 않는다.
#
#    실제로 그랬다. 대조 도구는 "$CFG 는 1단계 미구현" 이라고 적힌 채로
#    $CFG 가 구현된 뒤에도 한참을 통과했다 — 손으로만 돌렸기 때문이다.
#    빌드된 시험 바이너리가 필요하므로 자리는 여기다.
#
#    시험이 깨졌으면 대조는 건너뛴다. 깨진 바이너리로 대조해 봐야
#    무엇이 원인인지 흐려질 뿐이다.
if ($failed -eq 0) {
    $checks = @("check_sources.py", "crosscheck.py", "crosscheck_json.py", "crosscheck_crc.py",
                "crosscheck_cfg.py", "crosscheck_cfgtable.py",
                "crosscheck_hostlink.py", "crosscheck_i2c.py", "crosscheck_i2c_quantities.py")
    foreach ($c in $checks) {
        Write-Output "-- $c"
        cmd /c "chcp 65001 >nul && python $c"
        if ($LASTEXITCODE -ne 0) { $failed = 1 }
        Write-Output ""
    }
}
exit $failed
