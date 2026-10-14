# Agent-CAD V2 最终实施与验收报告

日期：2026-10-14。分支：`refactor/agent-cad-v2`。起点：`8a228ea`，工作区起初干净。本次只创建本地提交，没有 Push，也没有部署到生产。

**V2 的真实参数化生成、验证、有限修复、版本编辑和浏览器交付链路已经跑通。** 这次交付增加了实际工作的三维系统，保留旧二维接口；它还不是通用工业 CAD 或已完成生产部署验收的系统。复杂自然语言、多种设计约束与生产环境边界见后文，不能用固定语料的 100% 推断这些能力已经解决。

## 实际界面和产物

下图来自构建后的 Vue 页面与真实运行的本地后端，经浏览器登录、生成四孔板、把孔径从 6 改成 8 后截图；没有用生成图片替代 CAD。图中 GLB、下载 STEP 与 IR 来自同一 revision。

![真实浏览器中的四孔安装板](assets/cad-v2-workbench.png)

可直接检查同版 [STEP](assets/mounting-plate-8.step)、[GLB](assets/mounting-plate-8.glb)、[CAD IR](assets/mounting-plate-8.cad-ir.json)。实际外形 80×50×8 mm，四孔 Ø8，体积 30391.504561362 mm³。原 Ø6 历史版本下载后仍为 31095.221315766 mm³。

## 重构范围与架构

主链路为：自然语言或显式 IR → 结构化规划 → CAD IR 2.0 → 确定性编译 → 独立进程中的 build123d/OCP → 实际几何校验 → STEP 导出并重导入 → 原子发布不可变版本 → Three.js 预览及同版下载。可恢复的选择器精度错误进入有次数限制的局部修复，并重新走完整验证。

| 模块 | 主要文件 | 已完成职责 |
|---|---|---|
| IR 与领域接口 | `backend/app/domain/cad_v2/` | 强类型特征、单位、参数表达式、DAG、编辑补丁、CADBackend、结构化错误 |
| 工作流 | `backend/app/application/services/cad_v2_{planner,repair,service}.py` | 规划、持久任务、并发与队列限制、失败诊断、修复、编辑与恢复 |
| 内核与执行 | `backend/app/infrastructure/cad_v2/` | 精确 B-Rep、语义拓扑选择、实测验证、资源受限 worker、模型适配、SQLite |
| HTTP 与聊天接入 | `backend/app/interfaces/api/cad_v2_routes.py`、`cad_v2_app.py`、`backend/app/domain/services/tools/cad_v2.py` | 11 个 V2 路由、SSE、所有者隔离、独立启动、现有 AgentScope 的 4 个 V2 工具 |
| 三维界面 | `frontend/src/pages/CADV2Page.vue`、`components/CAD3DViewer.vue`、`composables/useCADV2Workspace.ts`、`api/cadV2.ts` | 真正加载 GLB、视角控制、参数重建、进度与错误、历史与恢复、同版导出 |
| 验证与评测 | `backend/tests/cad_v2/`、`backend/benchmarks/cad_v2/`、`frontend/tests/` | 单元/内核/服务/API/流程验收、30 项固定语料、在线现场证据、真实浏览器脚本 |
| 工程交付 | 两份依赖锁、`Dockerfile.cad-v2`、Compose、`.github/workflows/cad-v2.yml` | 轻量 CAD 独立安装、完整项目依赖、持久卷、CI 回归/构建/浏览器门槛 |

表内缩写文件路径以所在行明确的目录为根。完整关系和接口分别见 [架构](architecture-v2.md)、[IR](cad-ir-spec.md)、[Agent 工作流](agent-workflow.md)、[几何验证](geometry-validation.md)、[API](api-v2.md)、[迁移指南](migration-guide.md)。

实现了 Box/Cylinder/Sphere、Rectangle/Circle/Polyline 草图、Extrude、Revolve、Hole、Pocket、Fillet、Chamfer、Union/Cut/Intersection、Linear/CircularPattern、Transform。参数可编辑，新增/删除特征和依赖调整经 IR patch 重新编译；新增设计意图必须显式更新期望几何，修复逻辑不能自动放宽验收。

## 实际构建与测试

| 验证项 | 实际结果与范围 |
|---|---|
| 最终后端回归 | **157 passed，65.72 s**：`tests/cad_v2` + `test_agentscope_runtime.py` + `test_skills.py`。包含真实 OCP、受限子进程、几何负例、取消/超时、权限/CAS/幂等/重启、模型网关测试、版本编辑、修复及 STEP 重导入 |
| 原有 AgentScope/skills 回归 | 上述总数中包含原 10 项全部通过。原基线 9/10，已修复 ezdxf 文字对齐 API 枚举兼容问题；不是所有历史 HTTP/sandbox 测试通过 |
| 前端状态回归 | **8/8 通过**：失败保留有效版本、精确 revision、历史编辑保护、过期 SSE、防卸载后更新、断流轮询、SSE 打开失败、深链接恢复 |
| 前端构建 | `npm run type-check` 与 `npm run build` 通过；最终构建 14.75 s。V2 懒加载 JS 92.92 kB / gzip 30.62 kB；原主包仍为 3827.50 kB / gzip 1035.06 kB，有体积警告 |
| 真实浏览器 | **10 项检查通过**，Chromium Headless Shell 134.0.6998.35；无 page error、无失败 API 响应。证据：[browser-acceptance.json](assets/browser-acceptance.json) |
| 完整后端依赖 | `uv sync --locked --no-dev` 成功；锁解析 434 个跨平台依赖，当前环境检查 259 个安装包。完整环境可以导入 `app.main` |
| 集成路由 | 完整应用 OpenAPI 生成成功：58 个路径，其中 11 个 V2 路径；不代表已运行 Mongo/Redis/chat 生命周期 |
| 部署静态检查 | 三份 Compose 与 CI YAML 解析及命名卷引用检查通过，`git diff --check` 通过；源文件未发现真实 `sk-…` 密钥 |
| 尚未执行 | Docker 镜像构建、原完整 Mongo/Redis/sandbox/Claw 服务部署、GitHub Actions 云端执行；本环境没有 Docker，分支未 Push |

浏览器执行真实登录表单，提交中文安装板需求，经过 HTTP/SSE 与实际内核，等待 GLB 加载；操作六向/等轴、正交/透视、实体/线框、网格和坐标轴；参数面板改孔径，下载五种格式并比较 revision SHA-256；重新导入下载的 STEP；查看旧版并核对旧版 STEP；恢复为新版本，刷新页面验证持续存在。

浏览器验证中发现并修复独立模式 `local@localhost` 被通用邮箱校验拒绝的问题。该例外仅在 `cad_v2_standalone` 配置下启用，保留原应用本地认证及其他账号逻辑。另修正非 mm 文档的参数单位显示。最初桌面 Chromium 因环境禁止单实例套接字而无法启动；改用不依赖该功能的 Headless Shell 后完成验收。截图所需中文字体安装仅用于本次测试环境，未把字体包添加到项目依赖。

这次 GUI 测试验证上述按钮和流程，不把它等同于跨浏览器、移动端、所有鼠标手势、全部复杂零件或无障碍测试。

## 基准、模型与 A–E 验收

详见[完整基准报告](benchmark-report.md)，包含原始逐项 JSON、解析期望和分母。

- **固定 IR 语料：30/30**，基础 20、中等 10；真实几何有效、约束满足、STEP 导出与重导入全部通过。首次开发结果 26/30，修复了 Polyline、非 XY 草图世界原点、盲槽与通孔计数；前后语料 SHA-256 相同，没有修改期望值消除失败。
- 进程内完整几何任务均值 **0.1018 s**、P95 **0.3561 s**；不包含内核首次导入、子进程启动、HTTP、在线模型或浏览器。每例仅一次，不是生产吞吐指标。
- **应用流程预期结果 6/6**：A 中文四孔板；B 中文孔径 6→8；非法编辑准确失败并保留有效版；恢复；C 真实选择器精度失败并局部修复；D 双面 L 支架与 R2 圆角。E 由所有 STEP 真正重导入及独立尺寸/孔面检查落实。
- 有效编辑 **1/1**，构造的可修复失败恢复 **1/1**；如此小的分母不能代表一般编辑/修复成功率。D 使用显式 IR，不能称复杂自然语言支架规划已验证。
- **在线 `gpt-6.1-sol`：一款底板＋凸台＋贯穿孔设计完成真实模型→服务→内核→版本发布。** 现场共三次模型调用，先发现 `pi` 和最终特征约束支持问题；第三次服务发布成功后，独立验收脚本的空交集处理报错。修复验收脚本后，同一不可变 STEP 无重建、无额外模型调用，9/9 独立检查通过。全部失败记录保留。
- 第三次调用模型耗时 **35.995 s**，原验收运行 **41.591 s**，**19,943 tokens**；体积 35204.424506662 mm³、包围盒 80×50×20 mm、Ø6 中心通孔和 R10×12 凸台均有实测证据。没有基于一个设计宣称在线成功率。

旧仓库没有三维 B-Rep/STEP 基线，三维相对改善百分比 **不可计算**；26/30 是 V2 开发中的第一次结果，不是旧系统成绩。

## 实现程度与限制

| 状态 | 能力 / 边界 |
|---|---|
| 已实现、已验证 | 上述核心特征；真实 B-Rep 与 STEP 重导入；安全算术/依赖；参数和特征 patch；不可变版本与恢复；持久 job/SSE；owner/CAS/idempotency；有限局部修复；五种同版导出；浏览器主流程 |
| 部分实现 | 离线自然语言只覆盖严格完整匹配的板件/四孔语法；复杂需求走模型 JSON 后再本地验证。一般自然语言与缺失尺寸的澄清交互仍有限，不能保证任意机械描述正确理解 |
| 部分实现 | 自然语言编辑支持明确的全部/四孔孔径；通用添加、移动、指定子集修改仍需要结构化参数或 Feature Patch，没有完整连续语言编辑规划器 |
| 部分实现 | 自动修复仅唯一语义选择器的精度调整，默认 3 次、硬上限 5；不是通用几何 healing。不会删除孔、改明确尺寸或放宽期望几何换取成功 |
| 部分实现 | 已测孔径/孔位/贯通/盲深、尺寸/体积、圆角半径、倒角距离与最终表面保留。通用壁厚、任意对称/距离约束、中间特征坐标映射仍有限；不支持的约束标记 unverified 并阻止发布 |
| 未实现 | Sweep/Loft、复杂自由曲面、装配体、通用草图约束求解、通用拓扑历史命名、一般量纲代数、多模型视觉评估 |
| 运行限制 | 单 Linux 主机、单 API supervisor、SQLite 本地磁盘；不支持多个实例共用数据库或 NFS。列表分页、自动历史清理、生产资源压力及大模型长期稳定性尚未完成 |
| 隔离限制 | 可信 IR 解释器运行在资源限制子进程，剥离模型密钥；**不是 OS 文件系统/网络安全沙箱**。不能接受任意模型 Python 执行。Python 下载依赖本项目安装环境才能重放 |
| 部署待验 | 容器与 CI 配置已交付但未在目标环境运行；原完整聊天仍依赖其 Mongo/Redis/sandbox/browser。旧前端大包、旧 Pydantic 废弃警告还存在 |

持久目录中数据库和 artifacts 必须一起备份，当前产物索引保存绝对路径，恢复时需保持服务可见数据根路径。独立模式的固定私有 token 代表一个本地用户；登出不会撤销被复制的 token，应轮换环境配置并重启。生产集成沿用原用户认证。

## 本地提交与阶段对应

Phase 4–6 共享任务发布、验证和版本事务，按可运行纵向流程一起集成，再单独提交聊天与编辑验收；Phase 8 的基准在内核开发中已并行运行，提交时间早于最终前端截图。该顺序保留了每项成果的可验证性。

| Commit | 阶段与内容 |
|---|---|
| `c117ffc` | Phase 0：审计、基线与实施顺序 |
| `e474955` | Phase 1：CAD Backend ports 与错误解耦 |
| `213ee16` | Phase 2：typed IR 与安全参数化编译 |
| `3013f00` | Phase 3 / 5：真实内核、资源受限 worker、实测验证与 STEP |
| `ba235a5` | Phase 4–6：持久任务、修复、版本、API、恢复与权限 |
| `884290d` | Phase 6：聊天工具接入与增量设计意图测试 |
| `eec6cef` | Phase 8：在线试验发现的 pi / 最终特征约束及重放测试修复 |
| `28cd602` | Phase 8：30 项真实基准、验收场景、三次在线试验及原始失败证据 |
| `468dec8` | Phase 7：三维界面、浏览器实际验收、截图和同版下载 |
| `855eedd` | Phase 9：依赖锁、持久部署、CI、README 与启动说明 |
| 本报告所在文档提交 | Phase 9：最终架构/API/迁移/验收文档与可下载 CAD 样例；具体哈希见 `git log -1` |

列出此次完整提交：`git log --oneline 8a228ea..refactor/agent-cad-v2`。没有推送远端，仓库中的 CI 尚未触发。

## 如何运行与复现

优先用独立模式体验 CAD，免去旧聊天堆栈依赖。Linux、Python 3.12：

```bash
cd backend
uv venv --python 3.12 .venv-cad
uv pip install --python .venv-cad/bin/python -r requirements-cad-v2.txt
export CAD_V2_LOCAL_TOKEN='replace-with-your-private-ascii-token'
export CAD_V2_DATA_DIR="$PWD/.cad-v2-data"
.venv-cad/bin/python -m uvicorn app.interfaces.cad_v2_app:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端：

```bash
cd frontend
npm ci
BACKEND_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5173/chat/cad-v2`，邮箱 `local@localhost`，密码是自己设置的 token。离线示例见 README。复杂需求通过环境设置 `CAD_V2_API_BASE`、`CAD_V2_API_KEY`、`CAD_V2_MODEL=gpt-6.1-sol`，不要把密钥写入 IR 或版本库。

在装好 CAD 和测试依赖的环境里：

```bash
cd backend
python -m pytest tests/cad_v2 tests/test_agentscope_runtime.py tests/test_skills.py -q
python -m benchmarks.cad_v2.run --output /tmp/cad-v2-benchmark
python -m benchmarks.cad_v2.workflows --output /tmp/cad-v2-workflows
```

前端：`npm test && npm run type-check && npm run build`。在同一个 CAD Python 环境安装 Playwright 并安装 Chromium 后，从仓库根目录运行 `python frontend/tests/browser_cad_v2.py --output /tmp/cad-v2-browser`。完整依赖安装用 `cd backend && uv sync --locked`。具体最小 CI 测试依赖和浏览器安装命令已固化在 `.github/workflows/cad-v2.yml`。

## 后续优先级

1. 在用户实际部署环境完成 Docker、持久卷重启、备份恢复、JWT 多用户与反向代理 SSE 验收；如要接受任何不可信代码，先补完整 OS 沙箱。
2. 用独立人工编写、带预期工程约束的自然语言留出集评测模型；降低当前完整 schema 带来的约 18.7k 输入 token，保持同等约束强度。
3. 扩展连续自然语言特征编辑和缺失尺寸澄清；增加壁厚、相对距离、对称与变换后特征验证。
4. 基于冷启动测量优化 worker 启动成本，增加大零件与并发压力测试、存储配额、分页和版本保留策略；拆分现有前端主包。

本次没有将尚未实现的广泛自然语言理解、复杂几何验证或生产稳定性包装成已经验收的能力。
