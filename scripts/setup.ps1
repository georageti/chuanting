# 川听 Windows 一键安装
# 用法：在项目文件夹里，右键 scripts\setup.ps1 →“使用 PowerShell 运行”；
#       或者在 PowerShell 里运行：powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# 需要 Python 3.10 以上（python.org 安装时勾选 “Add python.exe to PATH”）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($msg) { Write-Host "== $msg" -ForegroundColor Green }

Step "1/5 找 Python"
$py = $null
foreach ($cand in @("py -3", "python", "python3")) {
  $parts = $cand.Split(" ")
  try {
    $ver = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]"3.10") { $py = $parts; break }
  } catch {}
}
if (-not $py) {
  Write-Host "没找到 Python 3.10 以上。请到 https://www.python.org/downloads/ 安装，安装时勾选 Add python.exe to PATH，然后重新运行。" -ForegroundColor Red
  Read-Host "按回车退出"; exit 1
}
Write-Host "   用的是：$($py -join ' ') （Python $ver）"

Step "2/5 建虚拟环境、装依赖"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py[0] $py[1..($py.Length - 1)] -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Step "3/5 下载 ECDICT 词典（约 63MB，MIT 许可；下载包里已带词典库时跳过）"
New-Item -ItemType Directory -Force "data\raw" | Out-Null
if (-not (Test-Path "data\dict.sqlite") -and -not (Test-Path "data\raw\ecdict.csv")) {
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv" -OutFile "data\raw\ecdict.csv.part"
  Move-Item "data\raw\ecdict.csv.part" "data\raw\ecdict.csv"
}

Step "4/5 生成词典库、跑自测"
if (-not (Test-Path "data\dict.sqlite")) { & .venv\Scripts\python.exe -I scripts\build_dict.py }
& .venv\Scripts\python.exe tests\run_tests.py | Select-Object -Last 1

Step "5/5 在桌面和开始菜单建“川听”快捷方式"
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
  $lnk = $ws.CreateShortcut((Join-Path $dir "川听.lnk"))
  $lnk.TargetPath = (Join-Path $root ".venv\Scripts\pythonw.exe")
  $lnk.Arguments = '"' + (Join-Path $root "run.py") + '"'
  $lnk.WorkingDirectory = $root
  $lnk.IconLocation = (Join-Path $root "assets\chuanting.ico")
  $lnk.Description = "川听：听语境，拼单词"
  $lnk.Save()
}

Write-Host ""
Write-Host "装好了。双击桌面上的“川听”打开（不会弹黑色窗口），用完在网页「设置」最下面点“关闭川听”。" -ForegroundColor Green
Read-Host "按回车退出"
