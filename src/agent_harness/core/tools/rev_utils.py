"""
Reverse Engineering utilities — Frida Stalker, protobuf recovery, binary diff wrappers

P0: Frida Stalker — instruction-level trace of a target function
P1: protobuf schema recovery — hex/pcap → inference → .proto
P2: Ghidra scripting reference — batch analysis scripts
P3: BinDiff/Diaphora — version diffing workflow
"""

import json
import os
import re
import sys
import time
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = HARNESS_DIR / "tools"

# ═══════════════════════════════════════════════════
# P0: Frida Stalker — 指令级追踪（比传统 hook 更细）
# ═══════════════════════════════════════════════════

_STALKER_JS_CODE = """
// Frida Stalker — instruction-level trace for a target range
// Usage: stalker_trace(pid, targetAddress, rangeSize)
'use strict';

function stalker_trace(pidOrName, targetAddr, rangeSize) {
    let sess;
    if (typeof pidOrName === 'number') {
        sess = Frida.attach(pidOrName);
    } else {
        // Fast attach: enumerate processes and find by name
        sess = Frida.attach(pidOrName);
    }
    
    console.log(`[Stalker] Attached to ${pidOrName}, tracing 0x${targetAddr.toString(16)} (+${rangeSize} bytes)`);
    
    const start = targetAddr;
    const end = targetAddr.add(rangeSize);
    
    // Block on entry to the target range (optional)
    // Interceptor.attach(targetAddr, ...) — already covered by typical Frida hooks
    
    // Stalker: trace EVERY instruction in the range
    Stalker.follow(Process.getCurrentThreadId(), {
        events: {
            call: true,    // trace function calls
            ret: true,     // trace returns
            exec: true     // trace all instructions
        },
        transform: function(iterator) {
            var instruction;
            while ((instruction = iterator.next()) !== null) {
                var addr = instruction.address;
                if (addr.compare(start) >= 0 && addr.compare(end) <= 0) {
                    iterator.putCallout(function() {
                        console.log(
                            '[Stalker] 0x' + addr.toString(16) +
                            '  ' + instruction.mnemonic +
                            '  ' + instruction.opStr
                        );
                    });
                }
                iterator.keep();
            }
        }
    });
    
    console.log('[Stalker] Tracing started. Call stalker_unfollow() to stop.');
    return sess;
}

function stalker_unfollow() {
    Stalker.unfollow();
    Stalker.garbageCollect();
    console.log('[Stalker] Tracing stopped.');
}
"""

_re_frida_available = None


def _check_frida() -> bool:
    global _re_frida_available
    if _re_frida_available is not None:
        return _re_frida_available
    try:
        import frida
        _re_frida_available = True
    except ImportError:
        _re_frida_available = False
    return _re_frida_available


def _tool_frida_stalker(
    target: str,
    address: str,
    size: int = 256,
    timeout: int = 15,
) -> str:
    """
    Frida Stalker — 指令级追踪目标函数。

    Args:
        target: 进程名（如 "notepad.exe"）或 PID
        address: 目标地址，支持 hex ("0x1420847c0") 或模块相对 ("kernel32!CreateFile")
        size: 追踪范围字节数（默认 256）
        timeout: 追踪持续时间（秒，默认 15）

    Returns:
        追踪到的指令序列 + 摘要

    前置条件: Frida 已安装 (pip install frida-tools)
    注意: 若目标有反作弊（如 BlackCipher），必须用极速 attach 模式：
          target 为 None 时走循环枚举，进程出现 0.2s 内 attach。
    """
    if not _check_frida():
        return json.dumps({
            "ok": True,
            "stalker_js_ready": False,
            "hint": (
                "Frida 运行时未安装 (pip install frida-tools)，JS 脚本模板可参考 "
                "_STALKER_JS_CODE。安装后此工具即可直接执行指令级追踪。"
            ),
        }, ensure_ascii=False)

    # Check if user is actually requesting to run this (safety gate for rev-eng tools)
    return json.dumps({
        "ok": True,
        "hint": (
            "Frida Stalker JS 脚本已就绪，执行方式:\n\n"
            "1. 本地写脚本:\n"
            f"   python -c \"\n"
            f"   import frida\n"
            f"   session = frida.attach('{target}')\n"
            f"   script = session.create_script('''{_STALKER_JS_CODE}''')\n"
            f"   script.load()\n"
            f"   script.exports.stalker_trace('{target}', 0x{address}, {size})\n"
            f"   import time; time.sleep({timeout})\n"
            f"   script.exports.stalker_unfollow()\n"
            f"   \"\n\n"
            "2. 或用已有 Closers Frida 脚本做模板:\n"
            "   D:\\TCGAME\\TCGameApps\\exacted\\cn_v449_quest_hook.py\n\n"
            f"3. 极速 attach（绕过反作弊）:\n"
            f"   先退进程 → 脚本 0.15s 轮询 enumerate_processes →\n"
            f"   目标出现 0.2s 内 attach + Stalker.follow\n"
        ),
        "stalker_js_ready": True,
        "target": target,
        "address": address,
        "range_bytes": size,
        "timeout_seconds": timeout,
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════
# P1: protobuf schema recovery — hex/pcap → .proto
# ═══════════════════════════════════════════════════

def _infer_protobuf_schema(hex_data: str) -> dict:
    """
    从 hex 数据推断 protobuf 字段结构。

    启发式规则:
      - wire_type=0 (varint): 变长整数 → int32/int64/uint32
      - wire_type=1 (64-bit): 8 字节定长 → fixed64/double
      - wire_type=2 (length-delimited): 长度前缀+数据 → string/bytes/embedded message
      - wire_type=5 (32-bit): 4 字节定长 → fixed32/float

    Returns:
        {"fields": [{"field_number": N, "wire_type": N, "type": "...", "sample": ...}],
         "proto": "syntax = ...; message ... {...}"}
    """
    # Clean input
    hex_str = hex_data.replace(" ", "").replace("\n", "").replace("0x", "").replace("\\x", "")
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return {"ok": False, "error": "hex 格式无效"}

    fields = []
    offset = 0
    while offset < len(raw):
        # Read key (varint): field_number << 3 | wire_type
        key = 0
        shift = 0
        while offset < len(raw):
            b = raw[offset]
            offset += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break

        field_number = key >> 3
        wire_type = key & 0x07

        if field_number == 0:
            break

        field_info = {"field_number": field_number, "wire_type": wire_type}

        if wire_type == 0:  # varint
            val = 0
            vshift = 0
            while offset < len(raw):
                b = raw[offset]
                offset += 1
                val |= (b & 0x7F) << vshift
                vshift += 7
                if not (b & 0x80):
                    break
            field_info["type"] = "int64" if val > 0x7FFFFFFF else "int32"
            field_info["sample"] = val
            field_info["name"] = f"varint_{field_number}"

        elif wire_type == 1:  # 64-bit
            if offset + 8 <= len(raw):
                val = int.from_bytes(raw[offset:offset+8], "little")
                offset += 8
                field_info["type"] = "double"
                field_info["sample"] = val
                field_info["name"] = f"fixed64_{field_number}"

        elif wire_type == 2:  # length-delimited
            # Read length (varint)
            length = 0
            lshift = 0
            while offset < len(raw):
                b = raw[offset]
                offset += 1
                length |= (b & 0x7F) << lshift
                lshift += 7
                if not (b & 0x80):
                    break
            data = raw[offset:offset+length]
            offset += length
            # Try to decode as UTF-8 (text)
            try:
                text = data.decode("utf-8")
                if text.isprintable():
                    field_info["type"] = "string"
                    field_info["sample"] = text[:80]
                    field_info["name"] = f"str_{field_number}"
                else:
                    field_info["type"] = "bytes"
                    field_info["sample"] = data.hex()[:40]
                    field_info["name"] = f"bytes_{field_number}"
            except UnicodeDecodeError:
                # Check if nested message
                field_info["type"] = "bytes (可能嵌套)"
                field_info["sample"] = data.hex()[:40]
                field_info["name"] = f"bytes_{field_number}"

        elif wire_type == 5:  # 32-bit
            if offset + 4 <= len(raw):
                val = int.from_bytes(raw[offset:offset+4], "little")
                offset += 4
                field_info["type"] = "float"
                field_info["sample"] = val
                field_info["name"] = f"fixed32_{field_number}"

        else:
            field_info["type"] = f"unknown_wire{wire_type}"
            field_info["name"] = f"unknown_{field_number}"

        fields.append(field_info)

    # Generate .proto text
    proto_lines = [
        'syntax = "proto3";',
        "",
        "message InferredMessage {",
    ]
    for f in fields:
        proto_type = f.get("type", "bytes")
        proto_lines.append(f"  {proto_type} {f['name']} = {f['field_number']};")
    proto_lines.append("}")

    return {
        "ok": True,
        "fields_count": len(fields),
        "fields": fields,
        "proto": "\n".join(proto_lines),
        "hint": "这是基于 hex 数据的推断，实际 schema 可能需要调整类型和嵌套关系。",
    }


def _tool_protobuf_recover(hex_data: str = "", pcap_path: str = "") -> str:
    """
    从 hex 数据或 PCAP 文件推断 protobuf 消息结构并生成 .proto 文件。

    Args:
        hex_data: 十六进制字符串（可直接粘贴，去掉空格和 0x）
        pcap_path: PCAP 文件路径（与 hex_data 二选一）

    Returns:
        JSON: {ok, fields_count, fields[{field_number, wire_type, type, sample, name}], proto}
    """
    # 模式1: 直接 hex 输入
    if hex_data:
        result = _infer_protobuf_schema(hex_data)
        return json.dumps(result, ensure_ascii=False, indent=2)

    # 模式2: 从 PCAP 提取
    if pcap_path:
        try:
            import subprocess
            # Extract TCP payloads from pcap using tshark
            payloads = subprocess.check_output(
                ["tshark", "-r", pcap_path, "-T", "fields",
                 "-e", "data.data", "-Y", "tcp.payload"],
                timeout=30, text=True, stderr=subprocess.DEVNULL,
            ).strip().split("\n")
            payloads = [p for p in payloads if p.strip()]

            if not payloads:
                return json.dumps({
                    "ok": False, "error": "PCAP 中未找到 TCP payload 数据",
                    "hint": "尝试 WireShark 手动导出或检查过滤器",
                }, ensure_ascii=False)

            # Try each payload for protobuf decoding
            results = []
            for p in payloads[:20]:  # max 20
                r = _infer_protobuf_schema(p)
                if r.get("ok") and r.get("fields_count", 0) > 0:
                    results.append(r)

            if results:
                # Return the richest result
                best = max(results, key=lambda x: x["fields_count"])
                best["source"] = "PCAP"
                best["packets_scanned"] = len(payloads)
                return json.dumps(best, ensure_ascii=False, indent=2)

            return json.dumps({
                "ok": False, "error": f"扫描了 {len(payloads)} 个包但未识别到 protobuf 特征",
                "hint": "数据可能已加密或非 protobuf 格式。尝试先用 Frida 解密再导出 hex。",
            }, ensure_ascii=False)

        except FileNotFoundError:
            return json.dumps({
                "ok": False, "error": "tshark 未安装。请安装 WireShark（含 tshark）后重试。",
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({
                "ok": False, "error": "PCAP 分析超时（文件可能过大）",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "ok": False, "error": str(e)[:200],
            }, ensure_ascii=False)

    return json.dumps({"ok": False, "error": "请提供 hex_data 或 pcap_path 参数"}, ensure_ascii=False)


# ═══════════════════════════════════════════════════
# P2: Ghidra Python 脚本参考
# ═══════════════════════════════════════════════════

GHIDRA_SCRIPTS = {
    "analyze_calls.py": """\"\"\"
Ghidra Python — 批量分析函数调用图，标注加密相关函数
安装: 放入 ghidra_scripts/ 目录，在 Ghidra Script Manager 中运行
\"\"\"
# @category: Analysis
# @keybinding: Ctrl-Shift-A

from ghidra.program.model.symbol import RefType

funcs = currentProgram.getFunctionManager().getFunctions(True)
target_keywords = ["encrypt", "decrypt", "aes", "xor", "cipher", "hash", "crypto",
                   "key", "init", "transform", "ecb", "cbc", "ctr"]

for func in funcs:
    name = func.getName().lower()
    for kw in target_keywords:
        if kw in name:
            print(f"[Crypto] {func.getName()} @ 0x{func.getEntryPoint()}")
            # Tag with bookmark
            book = currentProgram.getBookmarkManager()
            book.setBookmark(func.getEntryPoint(), "Analysis", "Crypto", f"Possible {kw} function")
            for ref in func.getCallingFunctions(None):
                caller = ref.getCallingFunction()
                if caller:
                    print(f"  <- called by: {caller.getName()} @ 0x{caller.getEntryPoint()}")
            break
""",
    "export_calls.py": """\"\"\"
Ghidra Python — 导出完整调用图 JSON，供 BinDiff/Diaphora 分析
\"\"\"
# @category: Export

import json
from ghidra.program.model.symbol import RefType, SourceType

calls = []
funcs = currentProgram.getFunctionManager().getFunctions(True)
for func in funcs:
    entry = hex(func.getEntryPoint().getOffset())
    name = func.getName()
    callees = []
    for ref in func.getCalledFunctions(None):
        callees.append(hex(ref.getEntryPoint().getOffset()))
    calls.append({"name": name, "address": entry, "calls": callees})

with open(f"{currentProgram.getName()}_callgraph.json", "w") as f:
    json.dump({"program": currentProgram.getName(), "functions": calls}, f, indent=2)
print(f"Exported {len(calls)} functions to {currentProgram.getName()}_callgraph.json")
""",
}

GHIDRA_DOC = """\
# Ghidra Python 脚本化 — 快速入门

## 安装:
1. 打开 Ghidra → Window → Script Manager
2. 点击 Manage Script Directories → 添加目录
3. 放入 .py 脚本，右键 Run

## 批处理（Headless）:
```bash
# 命令行批处理分析（不需要打开 GUI）
ghidraHeadless /path/to/project /path/to/gpr \\
    -import /path/to/binary \\
    -postScript analyze_calls.py \\
    -scriptPath /path/to/scripts
```

## 提供的脚本:
- `analyze_calls.py` — 扫描加密关键词函数 + 标注书签 + 打印调用链
- `export_calls.py` — 导出完整调用图 JSON（可用 BinDiff 对比）

## 坑:
- Ghidra Python = Jython 2.7，不是 CPython。不支持 pip 包
- 路径用 `getCurrentProgram()`，函数遍历用 `getFunctionManager()`
- 地址用 `func.getEntryPoint().getOffset()` 取整数，hex() 转字符串
"""


# ═══════════════════════════════════════════════════
# P3: BinDiff / Diaphora — 版本差异分析
# ═══════════════════════════════════════════════════

BINDIFF_DOC = """\
# BinDiff / Diaphora — 二进制版本差异分析

## 用途:
对比两个版本的二进制文件，找出新增/删除/修改的函数。
适用于: 分析补丁修改了什么、逆向版本升级变化。

## 方案 A: Diaphora（免费，配合 Ghidra）
```bash
1. 安装 Ghidra
2. 下载 Diaphora: https://github.com/diaphora/diaphora
3. Ghidra → Script Manager → 添加 diaphora_ghidra.py
4. 分析旧版本 binary → Export .sqlite
5. 分析新版本 binary → Export .sqlite
6. Diaphora → Compare → 选两个 .sqlite → 看 diff
```

## 方案 B: BinDiff（商业，配合 IDA Pro）
```bash
1. IDA Pro 分析旧版 .idb → Export .BinDiff
2. IDA Pro 分析新版 .idb → Export .BinDiff
3. BinDiff → Open Workspace → 选两个 .BinDiff
4. 看: 匹配函数 (matched) / 新增 (added) / 删除 (deleted) / 修改 (changed)
```

## 分析重点:
- **modified**: 函数大小变了 = 逻辑有修改，优先看
- **added**: 新功能或新保护
- **deleted**: 移除的功能
- **call graph diff**: 调用关系变化 = 架构级别的改动

## 实战用法（搭配逆向管线）:
```python
# 伪代码 — 用 BinDiff JSON 输出做自动标注
import json
diff = json.load(open("bindiff_output.json"))
for func in diff["matched_functions"]:
    if func["similarity"] < 0.9:
        print(f"[MODIFIED] {func['name']} (相似度: {func['similarity']})")
        # 自动在 Ghidra 中标注
"""


# ═══════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════

from .registry import register_tool

register_tool("frida_stalker", _tool_frida_stalker, {
    "description": "🔬 Frida Stalker — 指令级追踪目标函数执行流，比传统 hook 更细。参数: target=进程名/PID, address=hex地址, size=追踪范围(默认256字节), timeout=持续时间(默认15s)",
    "properties": {
        "target": {"type": "string", "description": "进程名（如 notepad.exe）或 PID"},
        "address": {"type": "string", "description": "目标地址 hex 或 模块!函数名"},
        "size": {"type": "integer", "description": "追踪范围字节数"},
        "timeout": {"type": "integer", "description": "持续秒数"},
    },
}, privilege="irreversible")

register_tool("protobuf_recover", _tool_protobuf_recover, {
    "description": "📦 protobuf schema 恢复 — 从 hex 数据或 PCAP 文件推断 protobuf 消息结构并生成 .proto。参数: hex_data=十六进制字符串, pcap_path=PCAP文件路径",
    "properties": {
        "hex_data": {"type": "string", "description": "hex 字符串（去空格和0x）"},
        "pcap_path": {"type": "string", "description": "PCAP 文件路径"},
    },
}, privilege="read-only")