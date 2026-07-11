"""Evaluation Dataset — 10 test tasks for Agent Harness regression testing.

Each task has:
  - id: unique identifier
  - request: the user's natural language input
  - expected_keywords: words that should appear in the output
  - expected_tool_used: tool that should have been called
  - category: "search" | "analyze" | "mixed"
  - weight: importance (1-3)

Run with: agent-harness eval
"""

EVAL_DATASET = [
    {
        "id": "eval-001",
        "request": "搜索今天的日期和星期几",
        "expected_keywords": ["星期", "202"],
        "expected_tool_used": "datetime",
        "category": "search",
        "weight": 1,
        "description": "Basic datetime query — tests tool routing",
    },
    {
        "id": "eval-002",
        "request": "计算 (123 + 456) * 789 的结果",
        "expected_keywords": ["456831", "456", "计算"],
        "expected_tool_used": "code_execute",
        "category": "analyze",
        "weight": 1,
        "description": "Simple code execution — tests analyze worker",
    },
    {
        "id": "eval-003",
        "request": "帮我总结这段文字的核心观点：人工智能正在改变软件开发的方式，AI Agent 可以自动完成代码编写、测试和部署，但人类的创造力和判断力仍然不可替代。",
        "expected_keywords": ["AI", "Agent", "人类", "人工"],
        "expected_tool_used": "summarize",
        "category": "analyze",
        "weight": 1,
        "description": "Text summarization — tests analyze worker with summarization",
    },
    {
        "id": "eval-004",
        "request": "搜索 2024 年诺贝尔物理学奖得主是谁",
        "expected_keywords": ["诺贝尔", "Hinton", "Hopfield"],
        "expected_tool_used": "search",
        "category": "search",
        "weight": 2,
        "description": "Web search for factual info — tests search worker",
    },
    {
        "id": "eval-005",
        "request": "读取 C:\\Users\\zwq\\agent-harness\\README.md 文件的前 100 个字",
        "expected_keywords": ["Agent", "Harness"],
        "expected_tool_used": "file_read",
        "category": "execute",
        "weight": 1,
        "description": "File reading — tests execute worker with file ops",
    },
    {
        "id": "eval-006",
        "request": "用 Python 写一个函数来判断一个数字是否为素数，然后判断 97 是不是素数",
        "expected_keywords": ["素数", "True", "97"],
        "expected_tool_used": "code_execute",
        "category": "analyze",
        "weight": 2,
        "description": "Code generation + execution — tests analyze worker end-to-end",
    },
    {
        "id": "eval-007",
        "request": "告诉我：1) 今天是什么日子 2) 用 Python 算一下 2 的 20 次方是多少",
        "expected_keywords": ["日期", "1048576", "2"],
        "expected_tool_used": "",  # multi-tool: datetime + code_execute
        "category": "mixed",
        "weight": 3,
        "description": "Multi-tool task — tests supervisor's ability to assign multiple workers",
    },
    {
        "id": "eval-008",
        "request": "把'Hello World'翻译成中文、日文、韩文三种语言",
        "expected_keywords": ["你好", "こんにちは", "안녕하세요"],
        "expected_tool_used": "think",
        "category": "analyze",
        "weight": 2,
        "description": "Translation — tests LLM reasoning without tools",
    },
    {
        "id": "eval-009",
        "request": "列出 5 个常用的 Python 标准库，并简要说明每个的用途",
        "expected_keywords": ["os", "sys", "json", "re", "datetime", "collections"],
        "expected_tool_used": "think",
        "category": "analyze",
        "weight": 1,
        "description": "Knowledge recall — tests LLM knowledge without external tools",
    },
    {
        "id": "eval-010",
        "request": "搜索AI Agent的最新进展，用 Python 把搜索结果按日期排序，然后总结前3条",
        "expected_keywords": ["Agent", "AI", "搜索"],
        "expected_tool_used": "",  # search + code_execute + summarize → multi-worker
        "category": "mixed",
        "weight": 3,
        "description": "Complex multi-worker task — tests full Supervisor-Worker pipeline",
    },
]

# Tasks that require network access (may fail offline)
NETWORK_TASKS = {"eval-004", "eval-010"}

# Tasks suitable for CI (no network required)
CI_TASKS = [t for t in EVAL_DATASET if t["id"] not in NETWORK_TASKS]
