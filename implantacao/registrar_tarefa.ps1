<#
Cria (ou recria) a tarefa agendada que roda o robo de segunda a sexta. Por padrao ela fica DESABILITADA.

  powershell -ExecutionPolicy Bypass -File implantacao\registrar_tarefa.ps1                 # 08:00, desabilitada
  powershell -ExecutionPolicy Bypass -File implantacao\registrar_tarefa.ps1 -Hora 07:30
  powershell -ExecutionPolicy Bypass -File implantacao\registrar_tarefa.ps1 -Habilitar      # depois da homologacao

Habilitar/desabilitar depois sem recriar:
  Enable-ScheduledTask  -TaskName "RPA Conciliacao AEROFLEX"
  Disable-ScheduledTask -TaskName "RPA Conciliacao AEROFLEX"
Rodar agora (teste):
  Start-ScheduledTask -TaskName "RPA Conciliacao AEROFLEX"

A tarefa roda como o usuario que executar este script e so quando ele estiver com a sessao aberta (pode ser sessao
de area de trabalho remota desconectada): o TOTVS WebAgent e o Chrome precisam da sessao do usuario.
#>
param(
    [string]$Hora = "08:00",
    [string]$Nome = "RPA Conciliacao AEROFLEX",
    [ValidateSet("executar", "ensaio", "planejar")][string]$Modo = "executar",
    [switch]$Habilitar
)
$ErrorActionPreference = "Stop"
$RepoBot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "rodar_dia.ps1"

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -Modo $Modo" `
    -WorkingDirectory $RepoBot
$gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Hora
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
$configuracao = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -Disable:(-not $Habilitar)

Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilho -Principal $principal -Settings $configuracao `
    -Description "Conciliacao bancaria AEROFLEX: API de leitura + robo (implantacao\rodar_dia.ps1). Log em saida\agendador." `
    -Force | Out-Null

$tarefa = Get-ScheduledTask -TaskName $Nome
Write-Output "Tarefa '$Nome' registrada para ${usuario}: seg a sex as $Hora, modo $Modo, estado: $($tarefa.State)"
