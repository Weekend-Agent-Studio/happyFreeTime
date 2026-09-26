<#
.SYNOPSIS
    启动 HappyFreeTime 后端，并显式选择本轮要启用的模型/召回模式。

.DESCRIPTION
    默认值仍然是完全离线的 Demo 配置，避免一次普通启动意外消耗 LLM
    额度。需要真实测试时只传参数即可，不需要手动设置多组环境变量。

    最常用的两种方式：

    1. 离线演示（默认）：
       .\scripts\start_backend_demo.ps1

    2. 完整真实链路（真实 Router + PlanningIntent + BGE Hybrid + Advisor）：
       .\scripts\start_backend_demo.ps1 -FullLlm

    3. 只打开部分能力，例如只测试真实 Advisor：
       .\scripts\start_backend_demo.ps1 -AdvisorMode llm

    LLM_API、MODEL_NAME、BASE_URL 等密钥和模型配置仍放在项目根目录的 .env
    中。脚本只负责设置 HFT_* 模式开关，不会把密钥写入命令行或仓库。

    -RouterMode demo|llm
        demo：离线 Demo Router；llm：真实 TurnInterpreter（原 RouterExtractor）。
    -PlanningIntentMode rule|llm
        rule：确定性 PlanningIntent；llm：受约束 LLM PlanningIntent，失败回退 rule。
    -RetrieverMode rule|hybrid
        rule：词法基线；hybrid：本地 BGE Hybrid。Hybrid 需要本地模型和索引，
        缺失时由应用按既有契约回退到 rule。
    -AdvisorMode rule|llm
        rule：确定性推荐解释；llm：真实 LLM 推荐解释，失败回退 rule。
    -FullLlm
        快捷设置为 Router=llm、PlanningIntent=llm、Retriever=hybrid、Advisor=llm。
        这里的“FullLlm”表示完整智能链路；Retriever 使用的是本地 BGE，而不是远程 LLM。

    重要：真实模式仍受 .env 中的 HFT_PROVIDER_MODE、HFT_ROUTE_PROVIDER_MODE 等
    Provider 配置控制。若只想验证 LLM，不需要打开真实高德路线/天气，保留 mock 即可。
#>

param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [int]$Port = 8000,
    [ValidateSet("demo", "llm")]
    [string]$RouterMode = "demo",
    [ValidateSet("rule", "llm")]
    [string]$PlanningIntentMode = "rule",
    [ValidateSet("rule", "hybrid")]
    [string]$RetrieverMode = "rule",
    [ValidateSet("rule", "llm")]
    [string]$AdvisorMode = "rule",
    [switch]$FullLlm
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# -FullLlm 是一个显式快捷预设。为兼容 Windows PowerShell 5.1，分支内直接
# 设置环境变量，不依赖参数变量的跨代码块赋值或动态集合索引。

Set-Location -LiteralPath $ProjectRoot

if (-not [System.IO.Path]::IsPathRooted($Python)) {
    $Python = Join-Path -Path $ProjectRoot -ChildPath $Python
}
$Python = [System.IO.Path]::GetFullPath($Python)
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python`nUse -Python `"$PWD\.venv\Scripts\python.exe`" or pass another existing interpreter."
}

# 逐个保存和设置环境变量。这里故意不用多行数组、Hashtable 或动态下标，
# 兼容 Windows PowerShell 5.1 和 PowerShell 7，也避免 NullArray 问题。
# 脚本可能在当前 PowerShell 进程中执行，因此退出时还会恢复这些值。
$previousHftDemoMode = [Environment]::GetEnvironmentVariable("HFT_DEMO_MODE", "Process")
$previousPlanningIntentMode = [Environment]::GetEnvironmentVariable("HFT_PLANNING_INTENT_MODE", "Process")
$previousRetrieverMode = [Environment]::GetEnvironmentVariable("HFT_CANDIDATE_RETRIEVER_MODE", "Process")
$previousAdvisorMode = [Environment]::GetEnvironmentVariable("HFT_RECOMMENDATION_ADVISOR_MODE", "Process")
$previousLangGraphStrict = [Environment]::GetEnvironmentVariable("LANGGRAPH_STRICT_MSGPACK", "Process")

# HFT_DEMO_MODE 是历史兼容变量：1 表示 Demo Router，0 表示真实 Router。
# 先设置普通参数模式，再由 -FullLlm 统一覆盖，避免嵌套 if/else 的兼容性问题。
$env:HFT_DEMO_MODE = "1"
if ($RouterMode -eq "llm") {
    $env:HFT_DEMO_MODE = "0"
}
$env:HFT_PLANNING_INTENT_MODE = $PlanningIntentMode
$env:HFT_CANDIDATE_RETRIEVER_MODE = $RetrieverMode
$env:HFT_RECOMMENDATION_ADVISOR_MODE = $AdvisorMode
if ($FullLlm) {
    $env:HFT_DEMO_MODE = "0"
    $env:HFT_PLANNING_INTENT_MODE = "llm"
    $env:HFT_CANDIDATE_RETRIEVER_MODE = "hybrid"
    $env:HFT_RECOMMENDATION_ADVISOR_MODE = "llm"
}
$env:LANGGRAPH_STRICT_MSGPACK = "true"

$exitCode = 0
try {
    Write-Host "HappyFreeTime backend"
    if ($FullLlm) {
        Write-Host "  Router:          llm"
        Write-Host "  PlanningIntent:  llm"
        Write-Host "  Retriever:       hybrid"
        Write-Host "  Advisor:         llm"
    }
    if (-not $FullLlm) {
        Write-Host "  Router:          $RouterMode"
        Write-Host "  PlanningIntent:  $PlanningIntentMode"
        Write-Host "  Retriever:       $RetrieverMode"
        Write-Host "  Advisor:         $AdvisorMode"
    }
    Write-Host "  Port:            $Port"
    Write-Host ""
    Write-Host "LLM_API / MODEL_NAME / BASE_URL are loaded from .env by the application."
    Write-Host "Provider modes remain controlled by .env (mock/replay/live)."
    Write-Host "Press Ctrl+C to stop."

    & $Python -m uvicorn app.api.server:app --host 127.0.0.1 --port $Port
    $exitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $previousHftDemoMode) { Remove-Item Env:HFT_DEMO_MODE -ErrorAction SilentlyContinue } else { $env:HFT_DEMO_MODE = $previousHftDemoMode }
    if ($null -eq $previousPlanningIntentMode) { Remove-Item Env:HFT_PLANNING_INTENT_MODE -ErrorAction SilentlyContinue } else { $env:HFT_PLANNING_INTENT_MODE = $previousPlanningIntentMode }
    if ($null -eq $previousRetrieverMode) { Remove-Item Env:HFT_CANDIDATE_RETRIEVER_MODE -ErrorAction SilentlyContinue } else { $env:HFT_CANDIDATE_RETRIEVER_MODE = $previousRetrieverMode }
    if ($null -eq $previousAdvisorMode) { Remove-Item Env:HFT_RECOMMENDATION_ADVISOR_MODE -ErrorAction SilentlyContinue } else { $env:HFT_RECOMMENDATION_ADVISOR_MODE = $previousAdvisorMode }
    if ($null -eq $previousLangGraphStrict) { Remove-Item Env:LANGGRAPH_STRICT_MSGPACK -ErrorAction SilentlyContinue } else { $env:LANGGRAPH_STRICT_MSGPACK = $previousLangGraphStrict }
}

exit $exitCode
