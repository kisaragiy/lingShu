#!/usr/bin/env bash
# BOSS 直聘自动打招呼 — 直调 greet API，跳过 show 查询
set -e
export PATH="$HOME/.local/bin:$PATH"
BASE_DIR="/c/Users/zwq/boss-agent-cli-dev"
GREETED_FILE="$HOME/.boss_greeted.txt"
TARGET=150

# 城市 + 关键词（广州/深圳重点，东莞/佛山/惠州补充）
declare -A CITY_KWS
CITY_KWS["广州"]="AI应用开发工程师 Python开发 LLM AIGC 人工智能 Agent开发 智能体"
CITY_KWS["深圳"]="AI应用开发 Python开发 LLM AIGC Agent开发 智能体 ComfyUI"
CITY_KWS["东莞"]="AI应用开发 Python LLM"
CITY_KWS["佛山"]="AI应用开发 Python"
CITY_KWS["惠州"]="AI应用开发 Python"

# 25届组合关键词（所有城市共用）
GRADUATE_KWS=("AI应用开发 25届" "Python 25届" "LLM 25届" "AIGC 25届" "Agent 25届")

touch "$GREETED_FILE"
cd "$BASE_DIR"

echo "================================================"
echo "BOSS 直聘自动打招呼 (v2)"
echo "每日目标: $TARGET"
echo "城市: ${!CITY_KWS[*]}"
echo "================================================"

greet_job() {
    local jid="$1"
    local sid="$2"
    local title="$3"
    local company="$4"
    local salary="$5"
    
    if [ -z "$sid" ]; then
        echo " ❌ 无 security_id"
        return 1
    fi
    
    local result
    result=$(python -c "
from boss_agent_cli.main import cli
from click.testing import CliRunner
import json
runner = CliRunner()
r = runner.invoke(cli, ['greet', '$sid', '$jid'])
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
        if [ "$err" = "ALREADY_GREETED" ]; then
            echo " ⏭️ 已打过招呼"
            echo "$jid" >> "$GREETED_FILE"
            return 0
        fi
        echo " ❌ ($err)"
        return 1
    fi
}

# 主循环
COUNT=$(wc -l < "$GREETED_FILE" 2>/dev/null || echo 0)
echo "已有 $COUNT 条记录"

while [ "$COUNT" -lt "$TARGET" ]; do
    for city in "${!CITY_KWS[@]}"; do
        [ "$COUNT" -ge "$TARGET" ] && break
        
        # 70% 普通关键词 + 30% 25届组合
        IFS=' ' read -ra kws <<< "${CITY_KWS[$city]}"
        keyword=""
        if [ $((RANDOM % 10)) -lt 3 ]; then
            g_idx=$((RANDOM % ${#GRADUATE_KWS[@]}))
            keyword="${GRADUATE_KWS[$g_idx]}"
        else
            k_idx=$((RANDOM % ${#kws[@]}))
            keyword="${kws[$k_idx]}"
        fi
        
        echo ""
        echo "=== $city / $keyword ==="
        
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
            for i in d.get('data', []):
                if isinstance(i, dict):
                    jid = i.get('job_id', '')
                    sid = i.get('security_id', i.get('securityId', ''))
                    title = i.get('title', '?')
                    company = i.get('company', '?')
                    salary = i.get('salary', '')
                    print(f'{jid}|{sid}|{title}|{company}|{salary}')
    except: pass
" 2>/dev/null)
        
        if [ -z "$result" ]; then
            echo "  无结果"
            continue
        fi
        
        echo "$result" | while IFS='|' read -r jid sid title company salary; do
            [ "$COUNT" -ge "$TARGET" ] && break
            
            if grep -q "^$jid$" "$GREETED_FILE" 2>/dev/null; then
                continue
            fi
            
            COUNT=$((COUNT + 1))
            echo -n "[$COUNT/$TARGET] $title @ $company $salary"
            greet_job "$jid" "$sid" "$title" "$company" "$salary"
            
            sleep $((8 + RANDOM % 12))
            
            if [ $((COUNT % 20)) -eq 0 ] && [ "$COUNT" -lt "$TARGET" ]; then
                rest=$((120 + RANDOM % 180))
                echo "  💤 已打 $COUNT 个，休息 $((rest / 60)) 分..."
                sleep "$rest"
            fi
        done
    done
    
    [ "$COUNT" -ge "$TARGET" ] && break
    echo ""
    echo "一轮完成，休息 5 分钟..."
    sleep 300
done

echo ""
echo "🎉 完成！共 $COUNT 个"
