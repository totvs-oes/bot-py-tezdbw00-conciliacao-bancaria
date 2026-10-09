<#
Execucao diaria do robo de conciliacao no servidor da cliente (chamado pelo Agendador de Tarefas).

  1. Garante o TOTVS WebAgent no ar (porta 21021).
  2. Sobe a API de leitura de extratos (porta 5000) se ela nao estiver no ar.
  3. Roda o robo: python -m conciliacao executar (data padrao = dia util anterior, com os FERIADOS do .env).
  4. Desliga a API se foi este script que a ligou.

Log em saida\agendador\<data_hora>.log. Codigo de saida = o do robo: 0 ok, 1 com pendencias, 2 falha
(3 = a API nao subiu). Em feriado (FERIADOS do .env) nao faz nada.

Uso manual:
  powershell -ExecutionPolicy Bypass -File implantacao\rodar_dia.ps1
  powershell -ExecutionPolicy Bypass -File implantacao\rodar_dia.ps1 -Modo ensaio -DataMovimento 2026-09-15
#>
param(
    [string]$DataMovimento = "",                                   # AAAA-MM-DD; vazio = dia util anterior
    [ValidateSet("executar", "ensaio", "planejar")][string]$Modo = "executar",
    [string]$RepoApi = "",                                         # padrao: pasta irma svc-py-tezdbw00-leitura-extratos
    [int]$PortaApi = 5000
)
$ErrorActionPreference = "Stop"
$RepoBot = Split-Path -Parent $PSScriptRoot
if (-not $RepoApi) { $RepoApi = Join-Path (Split-Path -Parent $RepoBot) "svc-py-tezdbw00-leitura-extratos" }
$PastaLogs = Join-Path $RepoBot "saida\agendador"
New-Item -ItemType Directory -Force $PastaLogs | Out-Null
$Carimbo = Get-Date -Format "yyyy-MM-dd_HHmm"
$Log = Join-Path $PastaLogs "$Carimbo.log"

function Escrever([string]$Mensagem) {
    $linha = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Mensagem"
    Write-Output $linha
    Add-Content -Path $Log -Value $linha -Encoding UTF8
}

function Porta-No-Ar([int]$Porta) {
    return [bool](Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue)
}

Escrever "Inicio: modo=$Modo data=$(if ($DataMovimento) { $DataMovimento } else { 'dia util anterior' })"

# --- Feriado (lista FERIADOS do .env do robo): a pasta do dia nao existe, nada a fazer -------------------------------
$ArquivoEnv = Join-Path $RepoBot ".env"
if (-not $DataMovimento -and (Test-Path $ArquivoEnv)) {
    $linhaFeriados = Get-Content $ArquivoEnv | Where-Object { $_ -match "^\s*FERIADOS\s*=" } | Select-Object -First 1
    if ($linhaFeriados) {
        $feriados = ($linhaFeriados -split "=", 2)[1].Split(",") | ForEach-Object { $_.Trim() }
        if ($feriados -contains (Get-Date -Format "yyyy-MM-dd")) {
            Escrever "Hoje e feriado (FERIADOS do .env): nada a fazer."
            exit 0
        }
    }
}

# --- 1. WebAgent ----------------------------------------------------------------------------------------------------
if (-not (Porta-No-Ar 21021)) {
    $webAgent = Join-Path $env:LOCALAPPDATA "Programs\web-agent\web-agent.exe"
    if (Test-Path $webAgent) {
        Escrever "WebAgent fora do ar: iniciando $webAgent"
        Start-Process -FilePath $webAgent
        Start-Sleep -Seconds 10
    } else {
        Escrever "AVISO: WebAgent nao encontrado em $webAgent (o login no Protheus vai falhar)"
    }
}

# --- 2. API de leitura ----------------------------------------------------------------------------------------------
$apiIniciada = $null
if (-not (Porta-No-Ar $PortaApi)) {
    $pythonApi = Join-Path $RepoApi "venv\Scripts\python.exe"
    Escrever "Subindo a API de leitura ($RepoApi)"
    # uvicorn direto, sem o reload do main.py: com reload sobra um processo filho segurando a porta depois do Stop.
    # 127.0.0.1: so o robo, nesta maquina, acessa a API.
    $apiIniciada = Start-Process -FilePath $pythonApi `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$PortaApi") -WorkingDirectory $RepoApi `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $PastaLogs "${Carimbo}_api.out.log") `
        -RedirectStandardError (Join-Path $PastaLogs "${Carimbo}_api.err.log")
    $noAr = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest "http://127.0.0.1:$PortaApi/docs" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $noAr = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $noAr) {
        Escrever "ERRO: a API de leitura nao respondeu em 60 s (ver ${Carimbo}_api.err.log)"
        if (-not $apiIniciada.HasExited) { Stop-Process -Id $apiIniciada.Id -Force }
        exit 3
    }
    Escrever "API de leitura no ar (porta $PortaApi)"
} else {
    Escrever "API de leitura ja estava no ar (porta $PortaApi)"
}

# --- 3. Robo --------------------------------------------------------------------------------------------------------
$codigo = 2
try {
    $argumentos = @("-m", "conciliacao")
    if ($Modo -eq "planejar") { $argumentos += "planejar" } else { $argumentos += "executar" }
    if ($Modo -eq "ensaio") { $argumentos += "--ensaio" }
    if ($DataMovimento) { $argumentos += @("--data-movimento", $DataMovimento) }
    $env:PYTHONIOENCODING = "utf-8"
    Escrever "Robo: python $($argumentos -join ' ')"
    $robo = Start-Process -FilePath (Join-Path $RepoBot ".venv\Scripts\python.exe") -ArgumentList $argumentos `
        -WorkingDirectory $RepoBot -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $PastaLogs "${Carimbo}_robo.out.log") `
        -RedirectStandardError (Join-Path $PastaLogs "${Carimbo}_robo.log")
    $codigo = $robo.ExitCode
    Escrever "Robo terminou com codigo $codigo (0 ok, 1 com pendencias, 2 falha). Log: ${Carimbo}_robo.log"
} finally {
    # --- 4. Desliga a API se foi este script que a ligou ------------------------------------------------------------
    if ($apiIniciada -and -not $apiIniciada.HasExited) {
        Stop-Process -Id $apiIniciada.Id -Force
        Escrever "API de leitura desligada"
    }
}
exit $codigo
