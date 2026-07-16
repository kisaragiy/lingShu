#!/usr/bin/env bash
# BOSS 直聘自动打招呼 — patched boss-agent-cli 版本
# 每天 9:00 开始，打到 150 个为止
# 用法: bash start_boss_greet.sh

export PATH="$HOME/.local/bin:$PATH"
BASE_DIR="/c/Users/zwq/boss-agent-cli-dev"
GREETED_FILE="$HOME/.boss_greeted.txt"
TARGET=150
COUNT=0

# 城市和关键词
CITIES=("广州" "深圳" "东莞")
KEYWORDS=("AI应用开发" "Python" "LLM" "AIGC" "人工智能" "Agent开发" "AI工程师" "Python后端")

touch "$GREETED_FILE"
cd "$BASE_DIR"

echo "================================================"
echo "BOSS 直聘自动打招呼 (patched)"
echo "每日目标: $TARGET"
echo "时间: 9:00 ~ 完成"
echo "城市: ${CITIES[*]}"
echo "================================================"

greet_job() {
    local jid="$1"
    local title="$2"
    
    # 从 search 结果的 job_id 反查 security_id
    # 先搜 detail 拿 security_id
    local detail
    detail=$(python -c "
from boss_agent_cli.main import cli
from click.testing import CliRunner
import json
runner = CliRunner()
r = runner.invoke(cli, ['show', '$jid'])
if r.output:
    try:
        d = json.loads(r.output)
        if d.get('ok') and d.get('data'):
            data = d['data']
            if isinstance(data, list):
                for i in data:
                    if i.get('job_id') == '$jid':
                        print(json.dumps(i))
                        break
            elif isinstance(data, dict):
                print(json.dumps(data))
    except: pass
" 2>/dev/null)
    
    if [ -z "$detail" ]; then
        echo "  ❌ 无法获取详情"
        return 1
    fi
    
    local security_id
    security_id=$(echo "$detail" | python3 -c "import sys,json; print(json.load(sys.stdin).get('securityId',''))" 2>/dev/null)
    local lid
    lid=$(echo "$detail" | python3 -c "import sys,json; print(json.load(sys.stdin).get('lid',''))" 2>/dev/null)
    
    if [ -z "$security_id" ]; then
        echo "  ❌ 无 security_id"
        return 1
    fi
    
    # 实际打招呼
    local result
    result=$(python -c "
from boss_agent_cli.main import cli
from click.testing import CliRunner
import json
runner = CliRunner()
r = runner.invoke(cli, ['greet', '$security_id', '$jid'])
print(r.output)
" 2>/dev/null)
    
    local status
    status=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
    
    if [ "$status" = "True" ]; then
        echo " ✅"
        echo "$jid" >> "$GREETED_FILE"
        return 0
    else
        local err
        err=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code','?'))" 2>/dev/null)
        echo " ❌ ($err)"
        return 1
    fi
}

# 主循环
while [ "$COUNT" -lt "$TARGET" ]; do
    for city in "${CITIES[@]}"; do
        [ "$COUNT" -ge "$TARGET" ] && break
        
        keyword="${KEYWORDS[$RANDOM % ${#KEYWORDS[@]}]}"
        echo ""
        echo "=== $city / $keyword ==="
        
        # 搜索
        result=$(python -c "
from boss_agent_cli.main import cli
from click.testing import CliRunner
import json
runner = CliRunner()
r = runner.invoke(cli, ['search', '$keyword', '--city', '$city'])
if r.output:
    try:
        d = json.loads(r.output)
        if d.get('ok'):
            data = d.get('data', [])
            if isinstance(data, list):
                for i in data:
                    jid = i.get('job_id', '')
                    title = i.get('title', '?')
                    company = i.get('company', '?')
                    salary = i.get('salary', '')
                    print(f'{jid}|{title}|{company}|{salary}')
    except: pass
" 2>/dev/null)
        
        if [ -z "$result" ]; then
            echo "  无结果"
            continue
        fi
        
        echo "$result" | while IFS='|' read -r jid title company salary; do
            [ "$COUNT" -ge "$TARGET" ] && break
            
            if grep -q "^$jid$" "$GREETED_FILE" 2>/dev/null; then
                continue
            fi
            
            COUNT=$((COUNT + 1))
            echo -n "[$COUNT/$TARGET] $title @ $company $salary"
            greet_job "$jid" "$title"
            
            # 随机间隔 8-20 秒
            sleep $((8 + RANDOM % 12))
            
            # 每 20 个休息 2-5 分钟
            if [ $((COUNT % 20)) -eq 0 ] && [ "$COUNT" -lt "$TARGET" ]; then
                rest=$((120 + RANDOM % 180))
                echo "  💤 已打 $COUNT 个，休息 $((rest / 60)) 分 $((rest % 60)) 秒..."
                sleep "$rest"
            fi
        done
    done
    
    if [ "$COUNT" -ge "$TARGET" ]; then
        echo ""
        echo "🎉 完成！今日已打 $COUNT 个招呼"
        break
    fi
    
    echo ""
    echo "一轮完成，休息 5 分钟..."
    sleep 300
done
