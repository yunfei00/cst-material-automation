<#
.SYNOPSIS
 一键执行 R001: CST 删除所有 P3-A 实测体积为零的 Solid，新工程保存并回读。
.EXAMPLE
 powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_r001.ps1
#>
[CmdletBinding()]
param(
    [string]$InventoryReport = "local-data\P1_Report.json",
    [string]$GeometryReport = "local-data\P3A_Geometry_Report.json",
    [string]$Destination = "",
    [int]$BatchSize = 50
)
$ErrorActionPreference = 'Stop'
if ($BatchSize -lt 1 -or $BatchSize -gt 50) {
    throw "BatchSize 必须在 1～50 之间"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot
try {
    $inventoryPath = (Resolve-Path -LiteralPath $InventoryReport).Path
    $geometryPath = (Resolve-Path -LiteralPath $GeometryReport).Path
    $geometry = Get-Content -LiteralPath $geometryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($geometry.task -ne 'geometry' -or $geometry.status -ne 'PASS' -or
        [string]::IsNullOrWhiteSpace($geometry.source)) {
        throw 'P3-A 报告不是有效的 PASS geometry 或缺少 source'
    }
    $sourceFile = (Resolve-Path -LiteralPath ([string]$geometry.source)).Path
    if ([System.IO.Path]::GetExtension($sourceFile).ToLowerInvariant() -ne '.cst') {
        throw 'P3-A source 不是 .cst 工程'
    }
    $now = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($sourceFile)
    if ([string]::IsNullOrWhiteSpace($Destination)) {
        $destinationFile = Join-Path (Split-Path -Parent $sourceFile) (
            "{0}_R001_Cleaned_{1}.cst" -f $baseName, $now
        )
    }
    else {
        $destinationFile = [System.IO.Path]::GetFullPath($Destination)
    }
    if (Test-Path -LiteralPath $destinationFile) {
        throw "输出 CST 工程已存在，禁止覆盖：$destinationFile"
    }
    $planFile = Join-Path $repoRoot ("local-data\P4C_R001_Plan_{0}.json" -f $now)
    $reportFile = Join-Path $repoRoot ("local-data\P4C_R001_Apply_{0}.json" -f $now)
    $env:PYTHONPATH = Join-Path $repoRoot 'src'

    Write-Host "========== 1/3 R001 生成完整删除清单 =========="
    Write-Host "原工程：$sourceFile"
    Write-Host "目标副本：$destinationFile"
    $planArguments = @(
        '-m', 'cst_material.cli', 'cleanup-plan',
        '--source', $sourceFile,
        '--inventory-report', $inventoryPath,
        '--geometry-report', $geometryPath,
        '--report', $planFile
    )
    python @planArguments
    if ($LASTEXITCODE -ne 0) {
        throw "R001 计划生成失败。诊断报告：$planFile"
    }
    $plan = Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($plan.status -ne 'PASS') {
        throw "R001 计划状态不正确：$($plan.status)"
    }
    if ($plan.delete_count -ne $plan.zero_volume_delete_count) {
        throw '脚本只执行 R001，不允许混入其他显式或变体删除规则'
    }
    if ([int]$plan.delete_count -eq 0) {
        Write-Host "P3-A 报告中没有 V=0 的 Solid，无需删除。"
        return
    }
    Write-Host "删除 $($plan.zero_volume_delete_count) 个零体积 Solid；保留 $($plan.expected_remaining_count) 个。"

    Write-Host "========== 2/3 CST 副本执行 Solid.Delete =========="
    $applyArguments = @(
        '-m', 'cst_material.cli', 'cleanup-apply',
        '--source', $sourceFile,
        '--dest', $destinationFile,
        '--plan', $planFile,
        '--batch-size', "$BatchSize",
        '--report', $reportFile
    )
    python @applyArguments
    $exitCode = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $reportFile)) {
        throw "未生成验收报告，退出码：$exitCode。副本可能未完成：$destinationFile"
    }
    $report = Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($exitCode -ne 0 -or $report.status -ne 'PASS') {
        Write-Warning "实际删除未通过完整验收。错误：$($report.error)"
        throw "不要使用未通过验收的副本：$destinationFile。详见报告：$reportFile"
    }

    Write-Host "========== 3/3 保存与重新打开回读通过 =========="
    [PSCustomObject]@{
        Status = $report.status
        OriginalSolidCount = $report.original_solid_count
        DeletedZeroVolumeSolidCount = $report.rule_r001_zero_volume_count
        TotalDeletedSolidCount = $report.deleted_solid_count
        RemainingSolidCount = $report.remaining_solid_count
        RemainingMaterialsUnchanged = $report.all_remaining_materials_unchanged
        NewCstProject = $report.destination
        LocalReport = $reportFile
    } | Format-List
    Write-Host '打开新 CST 工程比较流畅度；性能改善不由 Solid 数量单独保证。'
}
finally {
    Pop-Location
}
