# 灵枢防复现规则库 v2 — 新增（从 daily/2026-08-14 + 08-15 抽取）

> v2 增量：08-14 是 AIGC/GPU/训练天量教训（41KB/41处坑），08-15 是记忆治理+后端边缘（41KB/39处坑）。
> 本文件只列 v2 新增的规则（R-11 起），与 v1（R-01~R-10）合并构成完整规则库。
> 每条规则=真实发生过的事后教训，转成事前拦截。

## 规则 R-11   GPU / 显存竞争（ComfyUI 生成 vs ollama VLM）
- trigger: 即将在 12G 卡上并行跑「ComfyUI 生成 + ollama VLM 视觉」两类任务
- lesson: ComfyUI 生成 + ollama VLM 同抢 GPU 互相拖死（VLM 图像编码 117s/张超时；训练/生成期间并发 VLM 必拖死）。Windows 上真凶常是 **WSL 的 ollama 驻留显存**（qwen3-vl 驻留 6.5GB）
- block_hint: GPU 密集型任务**串行执行**；排查显存占用先查 WSL（vmmem/ollama）而非只看 nvidia-smi（不显示 GPU-PV 直通）；OLLAMA_HOST=0.0.0.0 + setsid nohup 重启释放

## 规则 R-12   模型显存需求预判
- trigger: 即将在 12G 卡跑重型模型（Flux.2 Klein/GSDX大图/训练）
- lesson: 复杂描述大尺寸（game_kv 1536x864）VRAM 压力 11861MiB/12282MiB → 生成超时；Flux.2 Klein 9B 17.2G > 12G → 动态加载死锁
- block_hint: 跑前**先算显存需求**（后端+模型+尺寸），超即降档（大图→低一档尺寸 / Klein→GGUF / 降级 SDXL 系）

## 规则 R-13   ComfyUI 僵尸任务 / 队列拥堵
- trigger: 即将提交生成，或遇到 cover 式"报超时但还在跑"
- lesson: 僵尸任务堵队列→后续生成全"假超时"（cover 报超时但 workflow 还在队列执行）；僵尸 interrupt 无效（卡非采样阶段）
- block_hint: 提交前**查队列深度**（打印排队+超时=timeout+排队数×90s）；遇到队列拥堵杀 ComfyUI 进程 + comfy_launcher.py 重启清空队列

## 规则 R-14   静默失败链（"假完成"）
- trigger: 即将验收一个多层管线的输出（enhance/restore 等串联步骤）
- lesson: enhance 损坏图"假完成"——去噪失败→超分失败→修脸跳过→仍报完成，静默失败链违反"100%可用/无静默失败"
- block_hint: 管线入口**前置校验**（缺图/损坏立即报错），任一层失败必须显式中止不静默通过；验收看每一步的真实结果

## 规则 R-15   VLM 对缩略图/小图误判
- trigger: 即将用 VLM 判断一张缩略图/被缩放的图（构图/文字/完整性）
- lesson: VLM 常把"小图/高度不够"误判成"文字截断/直角"（biztext 800x500 误判截断、assemble_page 圆角误判）——像素验证才是真
- block_hint: VLM 视觉判断**必须像素级验证兜底**（PIL/open_image_safe 真实读取）；或放大后再判，不单一信任 VLM 描述

## 规则 R-16   文档与代码脱节
- trigger: 即将交付一个"文档标 [x]"但代码没实现的功能
- lesson: CHAR-APPEAL 文档标已完成但 oc 代码没实现（文档代码脱节）→ 文档看似对齐实为虚
- block_hint: "文档标完成"≠代码完成。交付前**对照文档逐条验代码真实落盘**，不凭文档勾选当产出

## 规则 R-17   自动生成/自动提取的知识
- trigger: 即将把 LLM 反射器/自动管道提取的结果当事实入库
- lesson: 反射器自动提取 fact 364 有错（mistral vs qwen_3_8b 混淆）；反思器灌重复（73/75 vs GLM 三份）；自动管道需要人工校正
- block_hint: 自动提取/生成的知识**必须人工校一遍**；加噪音过滤（禁元描述/一次性信号/流水）防自动管道灌垃圾

## 规则 R-18   read_file 中文/二进制误报
- trigger: 读 .md / 中文内容文件，read_file 返回 binary 或空
- lesson: read_file 对中文/含特定字符的 .md 会误报 binary（daily 8/14、hermes-memory/index 第 2 例）——terminal 实际可读
- block_hint: read_file 报 binary 时用 terminal `cat`/`head` 兜底确认，不因工具误报判定文件损坏

## 规则 R-19   时间/日期判断
- trigger: 即将判定"某个 cron 跑没跑/某事件时间"（尤其凌晨任务链）
- lesson: 误判"4:00 备份没跑"其实是 02:31 未到点——教训：报时间判断先 `date` 确认（第 3 次踩）
- block_hint: 判断时间/周期事件前**先 `date` 拿真实当前时间**，不凭印象/记忆推断

## 规则 R-20   搜索源故障降级
- trigger: 即将依赖搜索（SearXNG / 外网 WebSocket / API）做查询
- lesson: SearXNG 引擎全挂 + Tavily key 失效；外网 ws 服务被墙；Docker 走 HTTPS_PROXY 无效（daemon 不走 shell 代理）
- block_hint: 搜索源故障时**web_extract 直抓权威文档**（URL 已知无需搜索）；外网被墙改本地起服务；Docker 代理需 Desktop 配置非环境变量

## 规则 R-21   patch 误删边界
- trigger: 即将对一个文件做多处 patch / 替换（尤其词库/大文件）
- lesson: 换 hand 词库时误删 pose 词库、补 p_gen 时误删 scene——替换边界没看准整体替换
- block_hint: 替换大段内容前**确认替换边界唯一且不误伤相邻代码**；用 replace（精确匹配）而非大面积 sed；改动前后 grep 确认关键锚点仍存在
