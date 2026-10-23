#!/usr/bin/env bash
# 安装仓库自带的 git hooks。
#
# 为什么需要它：.git/hooks/ 不进版本库，换机器/重新 clone 就丢。hook 里有两处
# 非显然的修复（grep -c 在 set -e 下静默退出、AGENTS.md 版本号抓取），丢了
# 会以"提交莫名失败"的形式重现，很难第一时间想到是 hook 的问题。
#
# 用法：
#   bash scripts/install-git-hooks.sh
#   git config --unset core.hooksPath     # 取消
set -e

REPO_DIR=$(git rev-parse --show-toplevel)
cd "$REPO_DIR"

if [ ! -d scripts/git-hooks ]; then
  echo "❌ 找不到 scripts/git-hooks/（请在仓库根目录运行）"
  exit 1
fi

git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/* 2>/dev/null || true

echo "✅ git hooks 已指向 scripts/git-hooks/"
echo "   当前生效的 hook:"
ls -1 scripts/git-hooks/
echo ""
echo "   取消: git config --unset core.hooksPath"
