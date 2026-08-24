# 灵枢防复现规则库 v3 — 求职线（从真实求职坑抽取）

> v3 求职线：用户求职主线（唯一目标）的真实坑，来源 = resume-builder skill("500投0面"教训)
> + daily/2026-08-23(主线卡点) + action-plan-20260823.md(资产盘点) + boss-job-search skill。
> 与 v1(技术复现)/v2(AIGC) 风格一致：每条=真实踩过+有明确"事前"拦截。
> 规则编号从 R-22 起（v1=R-01~10, v2=R-11~21）。

## 规则 R-22   简历措辞：产品暗示词（最大雷）
- trigger: 即将在简历项目经历里写"平台/产品/系统/PWA/APK/pip install/已上线"
- source: resume-builder skill + daily/2026-08-23.md
- lesson: "500 投 0 面"最大根源。写"AI 智能体编排平台""PWA+APK"会让面试官按产品标准追问用户量/上线网址/安装量——答不上就挂。相同工作量，"个人技术项目"预期完全不同
- block_hint: 用"框架/实践/工具链"替代"平台/产品/系统"。定位写"个人技术项目"而非"产品"。删掉 PWA/APK/pip install/上线词

## 规则 R-23   简历数字：可证伪性
- trigger: 即将写"30,000+ 行代码 / 62 个测试 / 62 个版本迭代"
- source: resume-builder skill + daily/2026-08-21.md(简历金律)
- lesson: 数字太齐像编的、GitHub 能证伪就是诚信问题。30 秒能被 GitHub 证伪的 claim 不写；"62 个测试"目录里没有=当面穿帮
- block_hint: 数字必须可验证(126张训练图/rank64/2000步这种有据可查的)；行数诚实(10,000+ 不是 30,000+)；测试数不写除非能对上

## 规则 R-24   简历：角色名/HR 可读性
- trigger: 即将在简历写角色名(Knives/Ha Eun)、专业术语、无上下文的项目名
- source: resume-builder skill
- lesson: HR 看不懂角色名/术语。项目名要直接说明做什么(如"AIGC 图像生产管线")，角色名删掉
- block_hint: 项目名=一句话说明做什么；删角色名/黑话；先一句话说清项目用途再展开

## 规则 R-25   投递：招呼语定制（500投0面）
- trigger: 即将群发"您好我对这个岗位很感兴趣"这类模板招呼语
- source: boss-job-search skill + resume-builder skill
- lesson: 群发模板是 500+ 沟通 0 面试主因。第一句就要告诉 HR 为什么匹配 JD
- block_hint: 每家花 30 秒看 JD，提取 2-3 个关键词嵌入首句；"您好，我做过 X(JD关键词)，和贵司匹配度很高"；每天≤5 家高质量，不群发 50 家模板

## 规则 R-26   投递：岗位错配
- trigger: 即将用"AI应用开发"裸词搜索并投递匹配出的岗位
- source: boss-job-search skill + lingshu-market-alignment.md
- lesson: "AI应用开发"同时匹配算法研究/运营/商务/视觉/销售，投到错配岗(3-5年/论文竞赛/天使投资/运营)白费。广州 AI 纯岗竞争烈(硕士+论文+大厂实习候选人)
- block_hint: 主投"Python 后端开发"+"AI 应用"交叉；用精准词"LLM应用开发/AI agent工程师"；本地过滤排除错配词

## 规则 R-27   投递：规模筛选（应届友好）
- trigger: 即将投递大型/初创公司或 AI 训练师/标注员岗
- source: daily/2026-08-21.md + boss-job-search skill
- lesson: 只投 20-500 人公司(应届友好+规模合适)；AI 训练师/标注/数据清洗是廉价劳动岗非 AI 开发
- block_hint: 只投 20-500 人；排除"数据标注/标注员/ai训练师/训练师/清洗"伪AI岗；排除"主管/总监/架构师"管理岗

## 规则 R-28   主线：系统工具建设代替主线行动
- trigger: 即将连续几天投入系统工具建设(污染防御/成本/watchdog/compactor)而求职动作未动
- source: daily/2026-08-23.md + action-plan-20260823.md
- lesson: 卡思路根因=主线(求职)副线(闲鱼)都停在"准备完成、动作未开始"，最近5天全是系统工具建设(正反馈舒适区)替代主线行动
- block_hint: 每天会话开始先查 action-plan-20260823.md 的 P0(简历定稿/投递/闲鱼上架)是否有到期；工具建设在主线有动作后才做

## 规则 R-29   简历：源头纠错（路径/版本）
- trigger: 即将编辑错误路径的简历(如 C:\面试\Resume.md)或用 CyC2018 模板冒充本人简历
- source: action-plan-20260823.md
- lesson: 记忆曾写"C:\面试\Resume.md"但目录不存在；Markdown-Resume/ 是 CyC2018 模板非本人；真实简历=微信里 Java 版 PDF。简历源错了全盘皆错
- block_hint: 编辑前先确认简历真实位置(微信 Java 版 PDF)和唯一维护脚本(构建脚本)；用 resume-builder 的源白名单(只从 C:\面试\Resume.md 读)

## 规则 R-30   投递：跟踪表
- trigger: 即将开始投递但没建跟踪表(公司|岗位|投递日|状态|下次动作)
- source: action-plan-20260823.md + 求职全流程(fact_store)
- lesson: 投递 0 记录、无跟踪表 → 投了忘、无后续动作、没法复盘。fact #326 框架："公司|岗位|投递日|状态|下次动作"
- block_hint: 投递前先建跟踪表(fact_store 维护, tags=job-application)；每投一条即记录；每日会话检查"下次动作"到期项
