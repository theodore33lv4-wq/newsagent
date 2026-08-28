# install_task.ps1 —— 注册每周一 08:00 的 newsagent 定时任务（Windows 任务计划程序）
# 用法：以管理员身份运行：powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
#       （可选）加 -Uninstall 移除任务

param(
    [switch]$Uninstall,
    [string]$Time = "08:00",
    [string]$TaskName = "newsagent-weekly"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$entry  = Join-Path $repoRoot "scripts\run_weekly.py"

Write-Host "== newsagent 定时任务安装 =="
Write-Host "仓库目录  : $repoRoot"

if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Host "已删除任务：$TaskName"
    exit 0
}

if (-not (Test-Path $python)) {
    Write-Host "[致命] 未找到虚拟环境 Python：$python" -ForegroundColor Red
    Write-Host "       请先在仓库目录执行：python -m venv .venv 然后 .venv\Scripts\pip install -e ."
    exit 2
}
if (-not (Test-Path $entry)) {
    Write-Host "[致命] 未找到入口脚本：$entry" -ForegroundColor Red
    exit 2
}

# 校验时间格式（HH:mm）
if ($Time -notmatch "^\d{2}:\d{2}$") {
    Write-Host "[致命] 时间格式应为 HH:mm，当前：$Time" -ForegroundColor Red
    exit 2
}

$cmd = "`"$python`" `"$entry`""
schtasks /Create /TN $TaskName /TR $cmd /SC WEEKLY /D MON /ST $Time /RL LIMITED /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "[致命] 任务创建失败（exit $LASTEXITCODE）" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "已创建任务：$TaskName（每周一 $Time 运行）" -ForegroundColor Green
Write-Host "命令      ：$cmd"
Write-Host ""
Write-Host "注意事项："
Write-Host "  1) 请确认系统时区为北京时间（服务器时钟影响周编号与触发时间）："
Write-Host '     tzutil /s "China Standard Time"'
Write-Host "  2) 首次建议手动验证："
Write-Host "     .venv\Scripts\python.exe scripts\run_weekly.py --limit 5"
Write-Host "  3) 立即运行一次可执行：schtasks /Run /TN $TaskName"
Write-Host "  4) 查看任务：schtasks /Query /TN $TaskName /V"
exit 0
