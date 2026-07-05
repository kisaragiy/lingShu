"""ComfyUI tools — image/video generation, LoRA management"""
import json
import os
import time

from ..pipeline.llm import _session, HARNESS_DIR, _post_cloud
from .registry import register_tool


def _tool_comfyui_text2img(prompt: str, workflow: str = "", retries: int = 2, lora: str = "") -> str:
    """ComfyUI 文生图 — 优化提示词后调用 ComfyUI 生成（可选 LoRA 注入）"""
    import re as _re
    try:
        import sys
        skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from comfyui_tools import optimize_prompt

        optimized = optimize_prompt(prompt)
        # LoRA 注入
        if lora:
            try:
                from comfyui_lora_tools import lora_inject_lora_into_workflow as _lora_inject
            except ImportError:
                pass  # LoRA 工具不可用则跳过
        try:
            from comfyui_tools import text_to_image, _ensure_comfyui_running
            _ensure_comfyui_running()
            result = text_to_image(optimized, workflow_name=workflow, max_retries=retries)
            # text_to_image 返回字符串，解析评分和路径
            score = "?"
            path = str(result)[:300]
            m = _re.search(r"评分[：:\s]+([\d.]+)", str(result))
            if m:
                score = m.group(1)
            m = _re.search(r"文件[：:\s]+(.+)", str(result))
            if m:
                path = m.group(1).strip()
            return f"ComfyUI 生图完成: {path} (审美评分: {score})"
        except Exception as e:
            return f"[comfyui] ComfyUI 调用失败 ({str(e)[:80]})。优化后提示词: {optimized[:200]}"
    except Exception as e:
        return f"[comfyui] 桥接失败: {e}"


def _tool_comfyui_img2img(prompt: str, image_path: str = "", workflow: str = "", denoise: float = 0.65) -> str:
    """ComfyUI 图生图 — 基于参考图生成新图（三视图/场景多角度核心工具）"""
    import base64 as _b64
    try:
        import sys
        skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from comfyui_tools import image_to_image, _ensure_comfyui_running, optimize_prompt
        _ensure_comfyui_running()
        optimized = optimize_prompt(prompt)
        from comfyui_tools import _extract_positive
        pos = _extract_positive(optimized) or prompt
        if image_path and os.path.isfile(image_path):
            with open(image_path, "rb") as f:
                b64 = _b64.b64encode(f.read()).decode()
            result = image_to_image(pos, b64, workflow_name=workflow, denoise=denoise)
        else:
            result = image_to_image(pos, "", workflow_name=workflow, denoise=denoise)
        return f"ComfyUI 图生图: {result}"
    except Exception as e:
        return f"[comfyui_img2img] 失败: {e}"


def _tool_comfyui_character_sheet(reference_output_path: str, style_prompt: str = "") -> str:
    """角色三视图 — 正/侧/背 + 特写，纯白背景，保持画风一致"""
    _three_view_template = (
        "将图中人物扩展为全身三视图。左侧：胸部以上特写。中间：全身正面图。右侧上部：全身侧面图。"
        "右侧下部：全身背面图。纯白背景，画风与原图完全一致，比例不变形不夸张。"
        "character reference sheet, turnaround, front view, side view, back view, "
        "clean white background, consistent art style, no distortion"
    )
    prompt = f"{_three_view_template} {style_prompt}" if style_prompt else _three_view_template
    return _tool_comfyui_img2img(prompt, reference_output_path, denoise=0.55)


def _tool_comfyui_scene_grid(reference_output_path: str) -> str:
    """场景多角度 — 四宫格，保持画风与光线一致，标注视角"""
    _scene_grid_template = (
        "参考该图像以四宫格形式生成四个不同机位的视图。左上标注'正面'，右上标注'侧面'，"
        "左下标注'俯视'，右下标注'仰视'。保持画风与光线完全一致。"
        "four panel grid layout, consistent lighting and style, "
        "labeled views: front, side, top-down, low angle"
    )
    return _tool_comfyui_img2img(_scene_grid_template, reference_output_path, denoise=0.55)


def _tool_comfyui_multi_grid(grid_prompts_json: str, global_style: str = "") -> str:
    """多宫格分镜 — 2x2/3x3 分镜网格，保持连续性与一致性"""
    import json as _json
    try:
        prompts = _json.loads(grid_prompts_json)
    except _json.JSONDecodeError:
        prompts = [grid_prompts_json]
    grid_size = len(prompts)
    rows = 2 if grid_size <= 4 else 3
    cols = (grid_size + rows - 1) // rows
    combined = (
        f"{global_style or '最佳质量动漫番剧插画, 赛璐璐平涂, 极简高对比度阴影'}"
        " 锐利清晰线稿, 干净利落色块, 平涂上色, 不要出现文字, Ufotable style "
        f"生成一张{rows}x{cols}网格图像，包含{grid_size}个连续分镜头，严格保持情节连续性与环境元素光线一致性。 "
        + " ".join(prompts)
    )
    return _tool_comfyui_text2img(combined[:4000])


def _tool_comfyui_text2video(prompt: str, workflow: str = "", frames: int = 81) -> str:
    """ComfyUI 文生视频"""
    try:
        import sys
        skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from comfyui_tools import text_to_video, _ensure_comfyui_running
        _ensure_comfyui_running()
        result = text_to_video(prompt, workflow_name=workflow, frames=frames)
        return f"ComfyUI 文生视频: {result}"
    except Exception as e:
        return f"[comfyui_text2video] 失败: {e}"


def _tool_comfyui_optimize_comic(raw_prompt: str, style_ref: str = "Production IG") -> str:
    """漫剧提示词优化 — 六维度扩写（画风/主体/环境/光影/镜头/质量词）"""
    try:
        import sys
        skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from comfyui_tools import optimize_prompt
        system = (
            f"你是全球顶尖AI视觉艺术总监，专注漫剧提示词设计。参考{style_ref}风格。"
            "对用户提供的画面描述，按六维度扩写："
            "1.画风定位 2.主体细节(神态/动作/材质/眼神) 3.环境与氛围 4.光影魔法(光源方向/丁达尔/逆光) "
            "5.镜头语言(景别/焦距/机位) 6.质量修饰词(8k,masterpiece,cinematic lighting)。"
            "严禁输出图片。只输出纯文本提示词。格式: [模型: ComfyUI/SDXL] 优化后提示词: ..."
        )
        import requests
        s = requests.Session(); s.trust_env = False
        r = _post_cloud({
            "model": "deepseek-v4",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": raw_prompt},
            ],
            "max_tokens": 1024, "temperature": 0.4,
        }, timeout=60)
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[comic_optimize] 优化失败: {e}"


def _tool_comfyui_lora_status() -> str:
    try:
        import sys
        hdir = os.path.join(os.path.dirname(HARNESS_DIR), "harness")
        if hdir not in sys.path:
            sys.path.insert(0, hdir)
        from comfyui_lora_tools import lora_status
        return lora_status()
    except Exception as e:
        return f"[lora_status] 失败: {e}"


def _tool_comfyui_lora_prepare(character_name: str, image_dir: str = "", tag_prefix: str = "") -> str:
    try:
        import sys
        hdir = os.path.join(os.path.dirname(HARNESS_DIR), "harness")
        if hdir not in sys.path:
            sys.path.insert(0, hdir)
        from comfyui_lora_tools import lora_prepare_dataset
        return lora_prepare_dataset(character_name, image_dir, tag_prefix)
    except Exception as e:
        return f"[lora_prepare] 失败: {e}"


def _tool_comfyui_lora_prepare_all(project_characters_json: str) -> str:
    try:
        import sys
        hdir = os.path.join(os.path.dirname(HARNESS_DIR), "harness")
        if hdir not in sys.path:
            sys.path.insert(0, hdir)
        from comfyui_lora_tools import lora_prepare_all_from_assets
        return lora_prepare_all_from_assets(project_characters_json)
    except Exception as e:
        return f"[lora_prepare_all] 失败: {e}"


# ─── Tool Registration ───

register_tool("comfyui_text2img", _tool_comfyui_text2img, {
    "description": "通过 ComfyUI 生成图片（需 ComfyUI :8188 运行）",
    "properties": {"prompt": "string", "workflow": "string", "retries": "integer", "lora": "string"},
}, privilege="reversible")
register_tool("comfyui_img2img", _tool_comfyui_img2img, {
    "description": "ComfyUI 图生图（基于参考图+提示词，三视图/多角度核心）",
    "properties": {"prompt": "string", "image_path": "string", "workflow": "string", "denoise": "float"},
}, privilege="reversible")
register_tool("comfyui_character_sheet", _tool_comfyui_character_sheet, {
    "description": "角色三视图（正面/侧面/背面+特写，纯白背景）",
    "properties": {"reference_output_path": "string", "style_prompt": "string"},
}, privilege="reversible")
register_tool("comfyui_scene_grid", _tool_comfyui_scene_grid, {
    "description": "场景多角度四宫格（正面/侧面/俯视/仰视）",
    "properties": {"reference_output_path": "string"},
}, privilege="reversible")
register_tool("comfyui_multi_grid", _tool_comfyui_multi_grid, {
    "description": "多宫格分镜（2x2/3x3 网格）",
    "properties": {"grid_prompts_json": "string", "global_style": "string"},
}, privilege="reversible")
register_tool("comfyui_text2video", _tool_comfyui_text2video, {
    "description": "ComfyUI 文生视频",
    "properties": {"prompt": "string", "workflow": "string", "frames": "integer"},
}, privilege="reversible")
register_tool("comfyui_optimize_comic", _tool_comfyui_optimize_comic, {
    "description": "漫剧提示词六维度优化（画风/主体/环境/光影/镜头/质量）",
    "properties": {"raw_prompt": "string", "style_ref": "string"},
}, privilege="reversible")
register_tool("comfyui_lora_status", _tool_comfyui_lora_status, {
    "description": "列出已训练的 LoRA 模型",
    "properties": {},
}, privilege="read-only")
register_tool("comfyui_lora_prepare", _tool_comfyui_lora_prepare, {
    "description": "准备角色 LoRA 训练数据集（Kohya_ss 兼容）",
    "properties": {"character_name": "string", "image_dir": "string", "tag_prefix": "string"},
}, privilege="reversible")
register_tool("comfyui_lora_prepare_all", _tool_comfyui_lora_prepare_all, {
    "description": "批量准备所有角色 LoRA 数据集",
    "properties": {"project_characters_json": "string"},
}, privilege="reversible")
