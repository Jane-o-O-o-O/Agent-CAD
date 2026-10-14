# Agent-CAD V2：任务、生成、修复与版本流程

本文对应 `backend/app/application/services/cad_v2_service.py`、`backend/app/infrastructure/cad_v2/store.py` 和 `backend/app/interfaces/api/cad_v2_routes.py`。旧 `/api/v1/cad` 二维接口继续保留，V2 使用独立 `/api/v1/cad-v2` 接口。

## 实际执行链路

1. 已认证用户提交自然语言或 CAD IR，两者必须且只能提供一种。
2. 服务持久保存 Job ID、请求内容、文档归属和幂等请求标识，记录 `created`、`queued` 事件。
3. 有并发上限的 supervisor 取任务，将状态改为 `running`。
4. `cad_v2_planner.plan` 对明确安装板需求走确定性解析；其他需求使用配置的结构化模型网关。缺失必要尺寸返回 `RequirementError`，不使用未经确认的默认尺寸。
5. `CADDocumentIR` 和 `compile_ir` 检查 schema、参数表达式、引用、特征依赖和顺序；无效输入不进入内核。
6. `CADExecutor` 启动单任务子进程，可信编译器驱动 build123d/OCP。执行环境不继承模型密钥或代理配置，不运行 LLM 任意 Python。
7. 子进程通过真实进度消息报告 `building`、`validating`、`exporting`。服务把这些消息保存为可重放的事件，而非按定时器伪造执行进度。
8. 几何验证、导出和 STEP 重新导入检查通过后，服务检查产物路径、内容哈希、非空文件和完整格式集合。
9. STEP、STL、GLB、IR、Python 文件写入同一个不可变 revision 目录。SQLite 事务同时保存 revision、比较并更新文档当前版本，并把 Job 置为 `completed`。

模型输出不是三维事实。只有真实内核 report 的 `status=completed` 且 `validation.status=passed` 才能发布 revision。`failed` 或 `unverified` 不会成为成功版本。

## Job 状态与恢复

| 状态 | 含义 |
|---|---|
| created | 已保存任务，还未入执行队列 |
| queued | 等待受限执行名额 |
| running | 正在解析、规划或构建 |
| validating | 内核实际校验、导出或事务发布 |
| repairing | 正在记录局部修复，准备再次构建 |
| completed | 有可下载且已绑定 revision 的验证通过结果 |
| failed | 保存结构化错误，旧有效版本保留 |
| cancelled | 已取消，不能发布新版本 |

`progress` 为 0–100 的阶段进度，不是预计完成时间。修复时会回到构建阶段，因此数值可能降低；终态失败不会伪装为100%的成功。

每个任务的私有请求体、状态、错误、修复记录和 SSE 快照写入 SQLite。重启时：

- 原来 `created/queued` 的任务重新排队。
- 正在执行、校验或修复的任务记为 `InfrastructureError`，不会声称跨进程恢复了原内核状态。
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
- `prompt`：自然语言参数修改；当前确定性编辑优先支持明确参数变化，无法可靠定位时请求澄清。

不能同时提交自然语言和显式 patch。编辑在完整 IR 副本上应用，然后重新构建、重新验证。失败和取消都不会替换已有有效 revision。两个任务从同一版本并发修改时，发布事务只允许一个成功；另一任务返回 `RevisionConflictError`，并保留可诊断的 Job。

恢复历史版本使用 `restore`，从历史 IR 再次构建并创建**新的 revision**。历史 revision 不被修改或删除。每个 revision 保存 parameter diff、feature diff、父版本、报告与产物manifest。界面、预览和下载必须使用同一个 `revision_id`。

`request_id` 在用户和操作范围内幂等：重复相同请求返回原 Job，不重复构建；同一标识配不同内容返回409。即使队列已满，原请求的重试仍返回已存在 Job。

## 有限自动修复

`cad_v2_repair.next_repair` 是保守修复策略，不是任意改尺寸的优化器。目前支持一种经过真实执行验证的恢复：

- 内核返回可恢复 `InvalidTopologyError`，指明某个圆角/倒角特征的语义边选择精度不足。
- 策略只提高 selector 的定位容差，最大0.001 mm，保留特征数量要求、实体尺寸和工程约束。
- 保存失败类别、特征、修改路径、前后值、理由、fingerprint和次数。
- 重新编译并执行，所有原约束仍须通过；重复fingerprint不再尝试。

默认最多3次修复，可配置为0–5。网络、依赖、超时等基础设施问题不会被当成可修改几何的理由。其他几何问题返回准确失败，不能宣称支持普适自动修复。

## API 契约

成功 JSON 外层为 `{"code":0,"msg":"success","data":...}`；`documents` 和 `revisions` 的 `data` 直接为数组。错误响应使用对应 HTTP 状态。Job 中的领域错误以 `code` 为规范字段，同时提供 `type` 兼容别名。

| 方法 | 相对 `/api/v1/cad-v2` 路径 | 行为 |
|---|---|---|
| POST | `/jobs` | `{prompt? 或 ir?, name?, request_id?}` →202 Job |
| GET | `/jobs/{id}` | 当前 Job 快照 |
| GET | `/jobs/{id}/events` | SSE `event: job`，`data`为完整快照，`id`递增 |
| POST | `/jobs/{id}/cancel` | 取消任务；终态重复调用保持原终态 |
| GET | `/documents` | 当前用户文档列表 |
| GET | `/documents/{id}` | 文档及当前 revision |
| GET | `/documents/{id}/revisions` | 不可变历史版本列表 |
| GET | `/documents/{id}/revisions/{rid}` | IR、报告、diff、产物manifest |
| POST | `/documents/{id}/edits` | expected_revision + patch或prompt →202 Job |
| POST | `/documents/{id}/restore` | `{revision_id, expected_revision, request_id?}` →202 Job |
| GET | `/documents/{id}/revisions/{rid}/artifacts/{format}` | 该版本的 STEP/STL/GLB/IR/Python |

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
| `CAD_V2_LOCAL_TOKEN` | 仅独立应用需要，未配置或过短会拒绝启动 |
| `CAD_V2_FRONTEND_DIST` | 可选，独立应用同源提供已构建前端 |

自定义模型配置由 `infrastructure/cad_v2/model_gateway.py` 管理，保持与几何代码分离；可通过 V2配置或已有 API环境变量注入。

## 验证与已知限制

`tests/cad_v2/test_service.py` 包含真实子进程内核的中文安装板生成、6→8 mm通孔编辑、STEP重新导入及实际语义选择失败后的局部修复。其余生命周期用例使用显式标注的 FakeExecutor 测CAS、归属、幂等、取消、重启、资源限制和产物路径，不能将这些Mock用例统计为内核几何成功率。

`tests/cad_v2/test_api.py` 验证鉴权、请求限制、SSE重放、revision绑定下载及409冲突。真正浏览器三维操作、完整Mongo/Redis/旧聊天回归应单独记录，HTTP200不等同于这些场景验收。

当前没有跨主机队列、后台任务续算、自动版本保留/清理策略或文档列表分页。长期多人部署需要进一步添加磁盘配额、分页、备份/恢复流程与可观测性；不能按当前实现宣称大规模分布式生产能力。隔离仅用于可信编译器，不是允许执行任意不可信Python的安全承诺。
