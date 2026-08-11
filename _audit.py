import urllib.request, json, sys

with open('/app/maxapi_server.py', 'r', encoding='utf-8') as f:
    src = f.read()

prompt = (
    "你是maxapi的全面审计者。以下是maxapi_server.py完整源码（约3050行），"
    "一个Python HTTP代理，将Anthropic Messages API请求转换为上游se.zzmax私有API。"
    "3个端点(/v1/messages, /v1/responses, /v1/chat/completions)，13模型，"
    "DSML工具解析，4阶段上下文压缩，EWMA自校准，流式重试。\n\n"
    "请做全面审计，找出：\n"
    "1. 逻辑bug（会导致错误结果或崩溃）\n"
    "2. 安全漏洞（注入、信息泄露、DoS）\n"
    "3. 协议兼容性（Claude Code/OpenAI SDK能否正常工作）\n"
    "4. 性能风险（内存泄漏、无限循环、阻塞）\n\n"
    "按严重性P0/P1/P2排序，给出具体函数名+问题描述+修复建议。"
    "只报真正的问题，不要报风格建议。限3000字。\n\n---SOURCE---\n" + src
)

req = json.dumps({
    'model': 'gpt-5.6-sol',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 4000,
    'stream': False
}).encode()

try:
    r = urllib.request.urlopen(urllib.request.Request(
        'http://localhost:8080/v1/chat/completions',
        data=req,
        headers={'Content-Type': 'application/json'}
    ), timeout=600)
    d = json.loads(r.read())
    print(d.get('choices', [{}])[0].get('message', {}).get('content', '')[:8000])
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
