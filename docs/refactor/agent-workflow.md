# Agent-CAD V2：任务、生成、修复与版本流程

本文对应 `backend/app/application/services/cad_v2_service.py`、`backend/app/infrastructure/cad_v2/store.py` 和 `backend/app/interfaces/api/cad_v2_routes.py`。旧 `/api/v1/cad` 二维接口继续保留，V2 使用独立 `/api/v1/cad-v2` 接口。

## 实际执行链路

1. 已认证用户提交自然语言或 CAD IR，两者必须且只能提供一种。
2. 服务持久保存 Job ID、请求内容、文档归属和幂等请求标识，记录 `created`、`queued` 事件。
3. 有并发上限的 supervisor 取任务，将状态改为 `running`。
4. `cad_v2_planner.plan` 对安装板需求走完整消费语句的确定性解析；其他需求先调用独立 `requirements` 角色，从原需求和用户补答提取带精确原文来源的需求清单，再调用 `planner` 角色生成 IR。需求提取不读取生成结果；本地确定性检查保留明确尺寸、孔数和功能特征，不能由模型遗漏或改写。缺少影响几何的必要信息时返回 `RequirementError`，Job 进入 `awaiting_input` 并展示具体问题。信息充分则直接生成。
5. `CADDocumentIR` 和 `compile_ir` 检查 schema、参数表达式、引用、特征依赖和顺序；需求覆盖检查再对照最终输出实际依赖的特征图及解析参数。结构化模型计划遗漏可验证需求时，只允许一次带原始需求清单和失败证据的重新规划；无法验证的功能不靠重复调用变成成功。
6. 服务在每次内核执行前再次检查需求覆盖。必须等待实体实测的检查可以延后到几何阶段；计划阶段已失败或无法验证的检查会阻止执行。
7. `CADExecutor` 启动单任务子进程，可信编译器驱动 build123d/OCP。执行环境不继承模型密钥或代理配置，不运行 LLM 任意 Python。
8. 子进程通过真实进度消息报告 `building`、`validating`、`exporting`。服务把这些消息保存为可重放的事件，而非按定时器伪造执行进度。
9. 几何验证和 STEP 重新导入检查通过后，worker 从同一最终 B-Rep 生成 DXF/SVG 三视图；服务检查产物路径、内容哈希、非空文件及基础必需格式集合。
10. 服务使用最终 B-Rep 实测报告再次执行需求覆盖检查，包括不能仅凭 IR 证明的主体包围尺寸。需求或几何检查存在 `failed/unverified` 时，保存诊断而不发布。
11. STEP、STL、GLB、DXF、SVG、IR、Python 文件写入同一个不可变 revision 目录。SQLite 事务同时保存 revision、比较并更新文档当前版本，并把 Job 置为 `completed`。

模型输出不是三维事实。只有真实内核 report 的 `status=completed` 且 `validation.status=passed` 才能发布 revision。`failed` 或 `unverified` 不会成为成功版本。

独立需求和最终覆盖证据保存在服务拥有的 revision `report.requirements`、`report.requirement_coverage`。创建请求中的 IR `metadata.requirements/requirement_coverage` 被剥离，不能伪造通过证据；直接提交 IR 仍须通过编译和真实几何验证，但没有原始自然语言需求就不会附加虚假的需求覆盖声明。恢复版本仅从可信历史 revision report 读取已有需求并重新核对。编辑继续使用现有 `EditPlan` 的范围及未修改几何保护，不额外宣称已有独立的编辑需求提取流程。

功能检查有明确的保守边界：存在一个名为“加强筋”的 Box，或名为“槽”的 Cut，并不能证明满足 Rib/Slot 的功能与形状要求。无确定性表示或验证器的需求保留为不支持或 `unverified`，不能在模型换个标签后发布。即使扫掠、放样、螺旋螺纹等已有原生操作，其参数、路径、轮廓和目标绑定仍须落入当前可核对范围；几何有效不等于所有功能或制造要求都已证明。

## Job 状态与恢复

| 状态 | 含义 |
|---|---|
| created | 已保存任务，还未入执行队列 |
| queued | 等待受限执行名额 |
| running | 正在解析、规划或构建 |
| validating | 内核实际校验、导出或事务发布 |
| repairing | 正在记录局部修复，准备再次构建 |
| awaiting_input | 缺少设计信息，持久保存具体问题，释放执行名额等待补答 |
| completed | 有可下载且已绑定 revision 的验证通过结果 |
| failed | 保存结构化错误，旧有效版本保留 |
| cancelled | 已取消，不能发布新版本 |

`progress` 为 0–100 的阶段进度，不是预计完成时间。修复时会回到构建阶段，因此数值可能降低；终态失败不会伪装为100%的成功。

每个任务的私有请求体、状态、错误、修复记录和 SSE 快照写入 SQLite。重启时：

- 原来 `created/queued` 的任务重新排队。
- 正在执行、校验或修复的任务记为 `InfrastructureError`，不会声称跨进程恢复了原内核状态。
- `awaiting_input` 保留问题及历次用户补答，重启后可继续同一文档，不重新占用执行名额。
- 删除未提交的临时执行目录和没有 revision 元数据的孤立产物目录；保留已提交版本。
- 正常关闭服务会取消排队及执行中的任务，等待执行器杀死子进程并清理临时文件。

进程锁保证同一数据目录只允许一个 supervisor。当前实现明确支持**单主机、单 API 进程、多个受限建模子进程**；不能把同一 SQLite/产物目录挂载给多个 API 副本或当成 NFS 分布式队列使用。

## 增量编辑和撤销

所有编辑必须给出 `expected_revision`。允许：

- `parameters`：修改已有可编辑参数的数值。
- `feature_patches`：按稳定 Feature ID 修改 `params/inputs/depends_on/label`，由 IR 层验证。
- `add_features/delete_features`：新增或删除特征；悬空引用会失败，不会自动删除其他工程要求。
- `output_feature`：显式选择新输出特征，避免新增特征后仍静默导出旧实体。
- `expected_geometry/constraints`：用户显式修改设计意图；前者按顶层字段合并、`holes`整组替换，后者为显式完整列表替换。新增或删除孔时可以同时调整对应孔验证和解析体积；未指定的预期尺寸保留。设计意图及输出特征的变更也记录于revision diff，修复策略不能使用这项权限。
- `prompt`：调用 `plan_edit`，将整个自然语言请求转换为受限的结构化 `EditPlan`，作为一次原子增量编辑处理；必要时返回澄清。
- 已有装配还支持 `instance_patches/add_instances/delete_instances/mate_patches`；实例放置与配合要求仍须通过相应校验，不能借移动操作静默更换零件定义或弱化配合。

### 结构化自然语言编辑与澄清

`cad_v2_planner.plan_edit(prompt, current_ir)` 先校验当前 IR，再尝试完整消费请求的离线语法；超出离线范围时调用 `editor` 角色的结构化模型网关。输出只允许 `EditPlan` schema 中的参数/特征/实例/配合补丁和 `intents`，不接受替换整份 IR 或代码。每条 intent 记录请求的精确原文片段、`modify/move/add/remove` 操作和稳定目标 ID，计划元数据写入 `ir.metadata.last_edit`。

当前离线编辑仅适用于无装配、无后续变换/布尔分支的 Box 加轴向通孔链：支持中英文明确孔子集的孔径修改、左右/上下移动、删除，以及在板中心加指定孔径的通孔，可一次处理多项请求并转换单位。例如“将左边两个孔的直径改成8mm，并将右边两个孔向左移动5mm，其他保持不变”。整句有任何不支持的后缀都会转向结构化规划，不会只执行已识别的前半句；无模型配置时返回 `InfrastructureError`，不会把配置问题伪装成设计追问。

`validate_edit_plan` 在内核执行前检查修改范围：保护稳定特征 ID、原有依赖连通性、未选孔的孔位/孔径与未涉及约束；共享参数会牵动范围外几何时要求明确意图。删除必须有明确删除请求；仅移动的 intent 不能改尺寸；变更包围尺寸或实体数量须有对应声明。计划可以同步修改受影响的完整孔清单和解析体积，但不能删除无关验证、放宽容差或通过切换 output_feature 隐藏原几何。检查通过后仍必须真实重建和验证，不把模型自述作为几何证据。

目标、方向、尺寸、共享参数影响或现有配合冲突无法确定时，规划器返回 `clarification` 或校验器抛 `RequirementError`。Job 以 `awaiting_input` 保存问题，当前有效 revision 不变。首次生成用补充 `prompt` 和 `clarification_job_id` 再提交 `/jobs`；修改用原 `expected_revision` 和相同字段提交 `/edits`。服务验证用户、文档、请求类型和基准 revision，沿用同一草稿或已有模型，把原需求与历次问答合并成新 Job。若仍缺信息则继续追问，已回答的项目不会被离线解析器重复询问。旧 `failed` RequirementError 任务保持兼容；几何失败与配置错误不能按澄清续答。

问答上下文持久保存，限 20 轮、16000 字符。每个问题只接受一个续答任务；同一 `request_id` 与相同内容的重试返回已有任务。页面 URL 保留等待中的 Job，刷新可恢复问题。普通聊天通过 AgentScope 原生外部交互事件暂停 `message_ask_user`；CAD 工具读到 `awaiting_input` 也会保存问题并进入会话 `WAITING`，补答时保留原需求及任务、文档和版本 ID。

安装板原需求同时包含“四个孔、五个孔”时，明确补答“四个孔”会解决孔数冲突，而长度、孔径、布置和已回答参数继续保留。仅询问孔数量时也可回答 `4`；同时询问孔数与厚度等多个参数时，纯数字不擅自绑定。跨轮冲突、补答本身仍矛盾时会继续澄清，明确纠正仅作用于对应参数。缺少厚度和纵向孔距可以分两轮补充；最终不属于离线四孔模式的数量交给结构化规划，不能静默按四孔生成。

不能同时提交自然语言和显式 patch。编辑在完整 IR 副本上应用，然后重新构建、重新验证。失败和取消都不会替换已有有效 revision。两个任务从同一版本并发修改时，发布事务只允许一个成功；另一任务返回 `RevisionConflictError`，并保留可诊断的 Job。

恢复历史版本使用 `restore`，从历史 IR 再次构建并创建**新的 revision**。历史 revision 不被修改或删除。每个 revision 保存 parameter diff、feature diff、父版本、报告与产物manifest。界面、预览和下载必须使用同一个 `revision_id`。

`request_id` 在用户和操作范围内幂等：重复相同请求返回原 Job，不重复构建；同一标识配不同内容返回409。即使队列已满，原请求的重试仍返回已存在 Job。

## 有限自动修复

`cad_v2_repair.next_repair` 是保守候选策略，不是任意改尺寸的优化器。只处理具有已知 Feature ID 的可恢复 `InvalidTopologyError`，并区分两条路径：

- **语义选择精度**：Fillet/Chamfer 选择器必须含预期数量，并由内核建议有效的更高定位容差。仅 mm 文档可走此路径，策略上限 0.001 mm；不改变尺寸、特征数量或工程约束。
- **拓扑恢复**：仅对 worker 标记 `healing_candidate=true` 的现存候选形体，依次尝试 `shape_fix`（1e-5 mm）、`orient_solids`（1e-7 mm）、`sew_closed_shells`（1e-5 mm）。策略通过受信任的 `execution_options.healing` 传递，原始 IR 完全不变；模型 metadata 没有开启或放宽它的权限。

healing 在形体深拷贝上执行；保留绝对体积、面积、包围盒、原面几何和内环、已有实体数，且限制拓扑容差。缝合只能用完整闭壳恢复容器，不能填缺面、堵孔或合并实体来消除失败。候选即使通过这些不变量检查，也必须再次通过所有原始几何约束与 STEP 重导入验证，才能成为新 revision。详细检查与限制见 [geometry-validation.md](geometry-validation.md)。

每次保存失败类别、特征、修改路径、前后值、理由、fingerprint、执行策略和次数；重复候选不再尝试。默认最多 3 次修复，可配置为 0–5，所有候选共享这个预算。网络、依赖、隔离不可用、超时、无形体的运算异常或原设计约束失败，不会被当成扩大容差或修改几何要求的理由。有限测试缺陷可恢复，不代表任意几何保证修复。

## API 契约

成功 JSON 外层为 `{"code":0,"msg":"success","data":...}`；`documents` 和 `revisions` 的 `data` 直接为数组。错误响应使用对应 HTTP 状态。Job 中的领域错误以 `code` 为规范字段，同时提供 `type` 兼容别名。

| 方法 | 相对 `/api/v1/cad-v2` 路径 | 行为 |
|---|---|---|
| POST | `/jobs` | `{prompt? 或 ir?, name?, request_id?, clarification_job_id?}` →202 Job；续答仅用prompt，不带ir/name |
| GET | `/jobs/{id}` | 当前 Job 快照 |
| GET | `/jobs/{id}/events` | SSE `event: job`，`data`为完整快照，`id`递增 |
| POST | `/jobs/{id}/cancel` | 取消任务；终态重复调用保持原终态 |
| GET | `/documents` | 当前用户文档列表 |
| GET | `/documents/{id}` | 文档及当前 revision |
| GET | `/documents/{id}/revisions` | 不可变历史版本列表 |
| GET | `/documents/{id}/revisions/{rid}` | IR、报告、diff、产物manifest |
| POST | `/documents/{id}/edits` | expected_revision + patch或prompt；可附 clarification_job_id →202 新Job |
| POST | `/documents/{id}/restore` | `{revision_id, expected_revision, request_id?}` →202 Job |
| GET | `/documents/{id}/revisions/{rid}/artifacts/{format}` | 该版本 manifest 中的 STEP/STL/GLB/DXF/SVG/IR/Python |

新 worker 生成的 `drawing.dxf` 和 `drawing.svg` 与三维文件绑定同一 revision。DXF 使用可编辑尺寸实体，SVG 用作三视图预览；基本总尺寸及受支持的孔位/孔径来自最终 B-Rep 实测，不自动补全公差、剖面或制造要求。旧 revision 如果没有二维产物，不会被静默替换或重写；请求不存在的格式返回404。

解析形状的图纸走精确 HLR 并保留受支持的原生曲线/NURBS。含 BSpline/Bezier 面的自由曲面采用同一最终 B-Rep 新生成网格的 OCCT 原生多边形消隐，偏差目标 0.01 mm、角偏差 0.15 rad、三角形预算 100000；其 DXF 是近似线段，报告明确 `occt_polygonal_hlr/bounded_tessellation` 及容差，DXF/SVG 也显示可见的近似投影说明。尺寸仍取精确实体测量，不能把这类二维线段称为精确曲线。离散或三角形预算失败仍阻止发布。

SSE 支持 `Last-Event-ID` 或 `after` 游标，终态后结束；断线不自动取消后台建模。必须携带 Bearer 认证，客户端使用支持请求头的 fetch 流式读取。文件响应提供 SHA-256 ETag 和 `X-CAD-Revision`，不会把旧预览链接重定向到最新版本。

每一个文档、Job、revision、SSE 和产物访问都限定当前用户，其他用户得到404；未认证得到401。CAD JSON请求在解析前限制为2.1MB，IR及编辑内容另有限额。旧文件上传接口不受这个CAD JSON限制影响。

## 运行配置

生产应用 `app.main:app` 复用现有认证。轻量独立应用 `app.interfaces.cad_v2_app:app` 用于不启 Mongo/Redis/浏览器沙箱的 CAD 运行与验收，**仍然要求认证**：

```bash
cd backend
# 使用已安装 V2 requirements 的 Python 环境
export CAD_V2_LOCAL_TOKEN="自行生成至少16字符的私有令牌"
export CAD_V2_DATA_DIR="/path/to/persistent/cad-v2-data"
python -m uvicorn app.interfaces.cad_v2_app:app --host 127.0.0.1 --port 8012 --workers 1
```

不要将真实令牌提交到仓库或放在URL中。独立应用的 `/api/v1/config/frontend` 返回 `cad_v2_standalone=true`，前端可据此隐藏需要完整服务栈的旧会话功能。内核进程不需要模型密钥。

独立应用可直接使用现有登录页：邮箱为 `local@localhost`，密码为自己配置的 `CAD_V2_LOCAL_TOKEN`。登录/刷新返回同一个本地Bearer令牌，登出清除浏览器保存的凭据；撤销该固定令牌需要修改环境变量并重启。连续10次失败登录会暂时限制登录一分钟。这是单用户本地模式，不替代生产应用的多用户JWT认证。

| 配置 | 默认/作用 |
|---|---|
| `CAD_V2_DATA_DIR` | backend 下 `.cad-v2-data`，数据库与产物必须一起持久保存/备份 |
| `CAD_V2_MAX_CONCURRENCY` | 2个并行建模任务 |
| `CAD_V2_MAX_QUEUE` | 最多32个运行或排队任务 |
| `CAD_V2_TIMEOUT_SECONDS` | 每次内核执行90秒 |
| `CAD_V2_MAX_REPAIRS` | 3次上限 |
| `CAD_V2_PYTHON` | 可单独指定装有build123d/OCP的执行器Python |
| `CAD_V2_ISOLATION` | 默认strict，要求Landlock ABI ≥ 3和libseccomp；不可用则失败。process-dev仅供显式开发退出 |
| `CAD_V2_LOCAL_TOKEN` | 仅独立应用需要，未配置或过短会拒绝启动 |
| `CAD_V2_FRONTEND_DIST` | 可选，独立应用同源提供已构建前端 |

自定义模型配置由 `infrastructure/cad_v2/model_gateway.py` 管理，保持与几何代码分离；可通过 V2配置或已有 API环境变量注入。

## 验证与已知限制

`tests/cad_v2/test_service.py` 包含真实子进程内核的中文安装板生成、6→8 mm通孔编辑、STEP重新导入及实际语义选择失败后的局部修复。其余生命周期用例使用显式标注的 FakeExecutor 测CAS、归属、幂等、取消、重启、资源限制和产物路径，不能将这些Mock用例统计为内核几何成功率。

`tests/cad_v2/test_api.py` 验证鉴权、请求限制、SSE重放、revision绑定下载及409冲突。真正浏览器三维操作、完整Mongo/Redis/旧聊天回归应单独记录，HTTP200不等同于这些场景验收。

`tests/cad_v2/test_natural_edits.py` 区分离线语法的真实几何重建与模拟结构化模型输出的计划保护测试；后者验证 schema、目标范围和约束保护，不证明真实模型理解成功率。`test_healing.py` 使用真实 OCP 人工缺陷验证三种候选及拒绝边界；`test_drawings.py` 独立解析 DXF 验证投影、单位和尺寸。测试运行结果应以对应日志为准，不从实现说明推导通过率。

`scripts/smoke_cad_v2_clarification.py` 使用真实 HTTP 与内核：孔数冲突补答后停在尺寸追问，`create` 保存状态，服务重启后 `verify` 核对应用启动标记及原问题，继续补厚度和孔距，下载 STEP/IR 并核对 SHA-256。独立 STEP 重导入用 OCP 圆柱面及实体布尔探针检查 80×50×8 mm、四个直径 6 mm 通孔、孔坐标和解析体积，保存实际报告。`all` 仅连续执行两阶段，明确不验证重启。`scripts/smoke_cad_v2_docker.sh` 将该流程与原 IR 版本持久化验收放在同一次容器重启前后；结果须以运行产物为准。

当前没有跨主机队列、后台任务续算、自动版本保留/清理策略或文档列表分页。长期多人部署需要进一步添加磁盘配额、分页、备份/恢复流程与可观测性；不能按当前实现宣称大规模分布式生产能力。strict隔离面向可信编译器，具有明确的OS执行策略；process-dev不具备该保证。两者都不是允许执行任意不可信Python的承诺。
