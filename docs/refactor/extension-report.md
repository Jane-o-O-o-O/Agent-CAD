# CAD 扩展交付与验收记录

本轮从 `0cac865` 继续实现；代码阶段提交为 `e589be1`、`e131b1c`、`f33d6b7`、`7c2f439`。旧 [V2 报告](final-report.md) 保留原始历史结果，本报告记录本轮扩展。经用户授权，分支 `refactor/agent-cad-v2` 已推送，发布与合并记录见 [PR #2](https://github.com/Jane-o-O-o-O/Agent-CAD/pull/2)。

## 云端验证补充（2026-10-14）

以下结果补充本地阶段记录。后端、前端和 CAD Docker 的已验证代码提交为 `919e84f`，对应 [GitHub Actions](https://github.com/Jane-o-O-o-O/Agent-CAD/actions/runs/38524550093)。

| 云端检查 | 实际结果 |
|---|---|
| 后端回归与基准 | **226 passed，1 skipped**；严格文件/网络拒绝测试通过。跳过的是“主机不支持 Landlock 时拒绝执行”的环境条件测试，因为当前主机支持 Landlock。固定 30 例内核基准全部通过 |
| 前端与真实浏览器 | **9 项前端测试、type-check、build、13/13 浏览器场景通过**；浏览器使用 `strict`，Landlock ABI 7，无 page error / API failure |
| CAD Docker | 镜像构建与真实小实体探针通过；Landlock/seccomp 文件与网络拒绝探针通过；创建、编辑、恢复的 3 个版本与 7 种导出通过；容器重启后 **21 个产物哈希全部一致** |
| 原七服务 Docker 栈 | 首轮前端代理因 Vite 拒绝 Compose 主机名而失败；修复后全部内部检查通过，但首页探针缺少 HTML 请求头。两项均已真实复现并修复；`b551f6e` 最终重跑已启动，用户要求停止检查并直接合并，未等待其完成 |

云端实际发现并修复了三处差异：`35fd00c` 为两个后端镜像补齐 OCP 需要的 OpenGL/X11/Expat 运行库，并加入真实几何构建探针，同时补上浏览器装配体测试夹具依赖的 pytest；`455dcb3` 修正无孔长方体应有 3 个不重复总体尺寸的验收断言，另用 ezdxf 独立确认了修改前后实际尺寸。没有放宽严格隔离或改用几何替身。

`919e84f` 随后修复全栈前端代理：Vite 精确允许 `frontend-dev`，本地真实 Vite 回归验证代理成功且未知 Host 仍被拒绝，前端共 9 项测试通过。首轮构建的后端 apt 安装约 37 分钟，因此为两个镜像加入兼容原默认值的 `USE_ALIYUN_MIRROR` 构建参数，云端使用官方 apt 源，保留原依赖和全部启动检查。

第二轮全栈内部检查全部通过，镜像构建缩短到约 5 分钟，但最终宿主首页探针缺少 `Accept: text/html`，触发 Vite 的 404。`b551f6e` 补齐实际浏览器导航请求头；直接抽取原 Python 探针，在真实 Vite 上复现修复前 404、验证修复后首页 DOM 与认证代理同时通过。没有接受错误状态码或删去检查。

这些是隔离的 CI 构建和验收结果，不代表已部署生产，也不扩展下表中自然语言编辑、几何修复和装配求解的能力边界。

## 已实现

| 能力 | 实际行为 | 边界 |
|---|---|---|
| 复杂自然语言编辑 | `plan_edit` 生成原子增量补丁；可组合孔子集改径、移动、增删，模型路径支持新增特征和装配实例编辑；缺信息时提出问题，`clarification_job_id` 保留原任务上下文续答 | 未承诺任意自然语言都能成功；未覆盖或违反保留规则的修改会拒绝，失败保留上一个有效版本 |
| 几何修复 | 对真实 OCCT 形状执行 ShapeFix、实体定向、闭合壳缝合；限制容差并检查体积、边界、面几何、实体数量和原始设计约束 | 不是任意坏模型的自动修复器；缺面、过大缺陷、填充封闭空腔或违反要求会失败。缺陷恢复测试使用明确标记的真实内核故障夹具 |
| 修复重放 | 可信策略随 revision report 保存；恢复指定历史版本时重用；编辑仅对解析后依赖子图不变的特征继承；下载的 Python 也包含策略 | IR metadata 不能授权修复，改变上游尺寸会使旧策略失效；所有重建仍重新验证 |
| 装配体 | 共享零件定义、独立实例、参数化位姿、颜色与材料、组件树、选择/隐藏/移动；STEP/GLB 保留组件身份，导出 BOM JSON/CSV | fixed/aligned/concentric 检查明确给定的位姿，不是通用约束求解器；碰撞检查依据真实实体交叠 |
| 二维工程图 | 最终 B-Rep 的 OpenCascade HLR 三视图，第三角投影；可见线、隐藏线、中心线、实测尺寸；原生 DXF 曲线与 DIMENSION，SVG 预览 | 不是完整制造图自动审签；尚无通用剖视、GD&T 和任意布局优化；SVG 的部分曲线采样误差在报告中说明 |
| 执行隔离 | 默认 `strict`：Landlock 文件访问规则、seccomp 默认拒绝、去除 capabilities、资源限制、进程取消与超时；不可用时拒绝执行 | 本地宿主 Landlock 返回 ENOSYS；云端已在 ABI 7 主机和 Docker 中通过真实文件/网络拒绝探针。`process-dev` 仍只适用于明确选择的本地开发 |
| Docker / CI | CAD 前后端严格隔离与持久化验收脚本；原七服务栈独立启动/API 探测；GitHub Actions 挂接后端、浏览器和两种 Docker 门禁 | 云端进度见上表；全栈门禁只覆盖 mock 启动/API，不代表生产或完整聊天工具链验收 |

## 本地阶段实测结果

| 检查 | 结果 | 可复查记录 |
|---|---|---|
| CAD 后端及相关旧功能回归 | **225 passed，1 skipped**；跳过项为宿主不支持的 Landlock 完整文件系统测试 | [测试结果](assets/extension/test-results.json) |
| 固定 IR 内核样例 | **30/30** 构建、几何、约束、导出均通过 | [内核基准](assets/extension/kernel-benchmark.json)；不代表自然语言成功率 |
| 前端状态测试、类型检查、生产构建 | **8/8**；type-check 与 build 成功 | 同上测试结果；保留既有 CSS、bundle 与 Browserslist 警告 |
| 真实浏览器 | **13/13**，无 page error / API failure | [浏览器记录](assets/extension/browser-acceptance.json)；包含二维加载、7 种下载、历史恢复、多操作自然语言编辑、装配移动与 STEP/BOM 独立检查 |
| 真实 `gpt-6.1-sol` 编辑 | **一次调用成功，13/13 独立验收**；37.589 秒总流程，提供方报告 22,725 tokens | [完整记录](assets/extension/live-edit/report.json) |
| Docker / 严格文件系统 / 云 CI | 本地阶段为环境阻塞或未运行；后续云端结果见本文补充 | [原始环境门禁记录](assets/extension/environment-gates.json) |

真实模型用例将已有 Ø6 孔移动到 `(20,10)`，并在 `(35,20)` 增加 `10×8` 矩形通槽。检查器重新导入导出的 STEP，独立核验包围盒、解析体积、孔位置/直径/贯通、槽内空隙、槽外材料、原板特征和高度约束。不是只检查模型自己给出的 expected geometry。该记录仅是一个用例，不推断无人值守成功率。原始模型配置密钥未写入仓库或工件。

## 实际输出

![二维工程图工作台](assets/extension/workbench-2d.png)

![装配体工作台](assets/extension/workbench-assembly.png)

- 四孔安装板：[DXF](assets/extension/mounting-plate-8.dxf)、[SVG](assets/extension/mounting-plate-8.svg)。
- 装配体：[STEP](assets/extension/assembly.step)、[BOM CSV](assets/extension/assembly-bom.csv)、[装配信息](assets/extension/assembly.json)。
- 真实模型增量编辑：[STEP](assets/extension/live-edit/edited/model.step)、[IR](assets/extension/live-edit/edited/ir.json)、[工程图](assets/extension/live-edit/edited/drawing.svg)。

## 部署验证脚本与范围

`bash scripts/smoke_cad_v2_docker.sh` 构建 CAD API 与前端，执行实际文件/网络拒绝探针、创建/编辑/恢复、7 种工件验证，重启后复查三个版本的 21 个不可变下载。默认 strict 不会自动退回开发模式。

`bash scripts/smoke_full_stack_docker.sh` 构建原七服务开发栈，验证 MongoDB、Redis、后端与 sandbox OpenAPI、前端代理、数据处理器及 mock completion。它使用独立项目、loopback 端口、mock 和认证旁路，仅适用于隔离测试；不验证真实模型、聊天工具执行、浏览器或 Claw，不是生产配置。

`.github/workflows/cad-v2.yml` 包含严格隔离必过检查、浏览器、CAD Docker 与原全栈 Docker 作业，并支持手动触发。本分支已按用户授权推送到 `Jane-o-O-o-O/Agent-CAD`，云端实际差异与修复记录见上文。目标生产主机仍需独立验证内核能力、认证、网络入口和持久化备份；本次没有部署生产服务。
