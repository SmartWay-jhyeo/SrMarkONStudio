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
    @{ exe = "test_hostlink.exe"; src = "test_hostlink.c ..\app\mk_hostlink.c ..\app\mk_framing.c ..\app\mk_json.c ..\app\mk_config.c ..\app\mk_cfgwire.c" },
    @{ exe = "test_config.exe";   src = "test_config.c ..\app\mk_config.c" },
    @{ exe = "test_cfgwire.exe";  src = "test_cfgwire.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c" },
    @{ exe = "test_crc.exe";      src = "test_crc.c ..\app\mk_crc.c" }
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
exit $failed
