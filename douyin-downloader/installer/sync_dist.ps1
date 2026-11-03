# -*- coding: utf-8 -*-
# 把 dist 下旧的版本目录改名为当前版本，并同步快捷方式。
# 约定（用户要求）：dist 只保留一份可运行目录，名字永远等于当前版本号；
# 升级时不要新建 <新版本> 目录，而是运行本脚本把旧目录改名过去。
#
# 版本号与 exe 名都从 setup.iss 读取（唯一来源，和 build_setup.ps1 一致）。
# 本文件不写中文字面量：PowerShell 5.1 会按系统 ANSI 码页读无 BOM 的 .ps1。
$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$root = Split-Path -Parent $here
$dist = Join-Path $root 'dist'

$raw = Get-Content -LiteralPath (Join-Path $here 'setup.iss') -Raw -Encoding UTF8
$verMatch = [regex]::Match($raw, '#define MyAppVersion "([^"]+)"')
if (-not $verMatch.Success) { throw 'Missing MyAppVersion in setup.iss.' }
$version = $verMatch.Groups[1].Value
$exeMatch = [regex]::Match($raw, '#define MyAppExeName "([^"]+)"')
if (-not $exeMatch.Success) { throw 'Missing MyAppExeName in setup.iss.' }
$exeName = $exeMatch.Groups[1].Value

$target = Join-Path $dist $version
$targetExe = Join-Path $target $exeName
$others = @(Get-ChildItem -LiteralPath $dist -Directory |
            Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' -and $_.Name -ne $version })

if ($others.Count -gt 1) {
    throw ("Multiple old version dirs under dist, please resolve manually: " +
           (($others | ForEach-Object { $_.Name }) -join ', '))
}
if ($others.Count -eq 1) {
    $old = $others[0]
    if (Test-Path -LiteralPath $target) {
        # 文件夹版（onedir）产物是 exe + _internal/；单文件版只有 exe。两种都放行，
        # 其它东西（用户数据等）一律不动，交给人处理。
        $allowed = @($exeName, '_internal')
        $left = @(Get-ChildItem -LiteralPath $target -Force)
        $extra = @($left | Where-Object {
            $allowed -notcontains $_.Name -and
            -not ($_.PSIsContainer -and (Test-Path -LiteralPath (Join-Path $_.FullName $exeName)))
        })
        if ($extra.Count -gt 0) {
            throw ("Target dir already has content other than the build output, please resolve manually: " +
                   $target + " -> " + (($extra | ForEach-Object { $_.Name }) -join ', '))
        }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    Rename-Item -LiteralPath $old.FullName -NewName $version
    Write-Output ('renamed: ' + $old.Name + ' -> ' + $version)
}
# PyInstaller 文件夹版（COLLECT）会多套一层以程序名命名的子目录，产物实际落在
# dist/<version>/<程序名>/ 里。这里把内容上提一级，让两种形态都收敛到 dist/<version>/
# 下的同一布局（exe + _internal/），安装脚本和快捷方式只认这一层。
if (Test-Path -LiteralPath $target) {
    $subdirs = @(Get-ChildItem -LiteralPath $target -Directory -Force -ErrorAction SilentlyContinue)
    foreach ($sd in $subdirs) {
        if (Test-Path -LiteralPath (Join-Path $sd.FullName $exeName)) {
            Get-ChildItem -LiteralPath $sd.FullName -Force | Move-Item -Destination $target -Force
            Remove-Item -LiteralPath $sd.FullName -Recurse -Force
            Write-Output ('flattened: ' + $sd.Name + ' -> ' + $version)
        }
    }
}
if (-not (Test-Path -LiteralPath $targetExe)) {
    throw ('Missing ' + $targetExe + ', build the exe first.')
}

# 同步快捷方式：桌面 + 目录内（按旧 Target 指向 dist 下的同名 exe 来识别）
$sh = New-Object -ComObject WScript.Shell
$lnks = @()
$desktop = Join-Path $env:USERPROFILE 'Desktop'
if (Test-Path -LiteralPath $desktop) {
    $lnks += @(Get-ChildItem -LiteralPath $desktop -Filter '*.lnk' -ErrorAction SilentlyContinue)
}
$inDir = Join-Path $target ($exeName + '.lnk')
if (Test-Path -LiteralPath $inDir) { $lnks += @(Get-Item -LiteralPath $inDir) }

$updated = 0
foreach ($f in $lnks) {
    $l = $sh.CreateShortcut($f.FullName)
    if ($l.TargetPath -like '*\dist\*' -and (Split-Path -Leaf $l.TargetPath) -eq $exeName) {
        $l.TargetPath = $targetExe
        $l.WorkingDirectory = $target
        $l.Save()
        $updated++
        Write-Output ('shortcut updated: ' + $f.FullName)
    }
}
# 只保留当前版本的安装包（用户要求：旧版本安装包不留）
$setups = @(Get-ChildItem -LiteralPath $dist -Filter '*-setup.exe' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike ('*-' + $version + '-setup.exe') })
foreach ($s in $setups) {
    Remove-Item -LiteralPath $s.FullName -Force
    Write-Output ('removed old setup: ' + $s.Name)
}
Write-Output ('OK: ' + $targetExe + ' | shortcuts updated: ' + $updated)
