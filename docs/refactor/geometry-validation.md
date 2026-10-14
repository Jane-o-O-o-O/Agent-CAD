# 几何执行、验证与交付边界

实现：`backend/app/infrastructure/cad_v2/backend.py`、`validation.py`、`drawings.py`、`healing.py`、`worker.py`、`executor.py`。领域契约与静态校验位于 `backend/app/domain/cad_v2/`。已锁定并实际测试 build123d 0.11.1 / OpenCascade OCP 7.9.3.1.1；建模结果是精确 B-Rep，STL/GLB 仅作为其派生产物。

## 坐标与特征

所有编译后几何统一 mm、右手坐标系、Z 向上。Box 的 origin 是世界坐标最小角；Cylinder 的 origin 是 +Z 轴底面中心；Sphere 是 center。Rectangle 在局部草图平面取最小角，Circle 取中心，Polyline 使用显式二维顶点。先将草图放到 XY/XZ/YZ 平面，再按世界坐标 origin 平移。Extrude 沿草图法向；Hole/Pocket 采用显式 direction；Transform 按世界 X、Y、Z 轴顺序绕原点旋转，再平移。

真实内核实现 Box、Cylinder、Sphere、Rectangle/Circle/闭合 Polyline、Extrude、Revolve、Hole、Pocket、Fillet、Chamfer、Union/Cut/Intersection、LinearPattern、CircularPattern、Transform。阵列默认保留多个实体，`fuse=true` 执行真实并集；expected_geometry.solids 必须与目标一致。开放折线不能用于生成实体。

`Build123dBackend` 提供 build、validate、properties、query_topology、tessellate、export 和 cleanup。语义查询使用类型、方向、中心、邻近位置、范围和预期数量，不向调用者暴露可跨版本复用的面/边索引。

## 独立几何检查

每条检查包含 name、status、expected、actual、tolerance 和 evidence。状态明确区分 passed、failed、unverified。无法测量的要求不会自动通过。当前发布流程要求所有检查 passed；失败或未确认的任务保留诊断，禁止覆盖有效版本。

| 项目 | 实际证据 |
|---|---|
| 有效实体 | OpenCascade BRepCheck_Analyzer、实体遍历、正体积 |
| 包围盒和体积 | 内核边界与质量属性，对照独立 expected_geometry |
| 孔径与孔位 | BRepAdaptor_Surface 读取实际内向圆柱面的半径、轴线位置和方向；完整 2π 壁面 |
| 通孔 | 沿孔轴跨越整个零件包围范围构造孔内检查圆柱，与实际 B-Rep 做交集，确认无剩余材料 |
| 盲孔 | 实际圆柱面的轴向 V 区间、孔内空腔交集、孔底之后的实体点分类 |
| 圆角 | 新增圆柱/圆环等实际曲面半径与操作前后体积、拓扑变化；最终模型表面几何签名保留检查 |
| 倒角 | 新增平面顶点到原选择边的实际距离，与最终平面法向、偏距、空间范围匹配 |
| STEP | 实际写出后重新导入，同实体数、有效性、体积、包围盒最小/最大点比较，并对重导入实体重跑所有几何要求 |

`expected_geometry.holes` 非空时，表示**最终指定贯通/盲孔类别的完整孔轮廓清单**。验证器同时检查每个孔和该类别内向完整圆柱面的唯一轴线/半径数量，拒绝额外的第五个通孔。通过真实空腔交集区分贯通孔和盲圆槽；仅要求通孔时，不把指定的盲圆形 Pocket 算作额外通孔。相同轴线上不同半径作为不同轮廓；复杂沉孔、相交孔和复合孔型尚不作为通用保证。孔切到外边界形成缺口会因为缺少完整圆柱壁而失败。

dimension、volume、solid_count 约束未指定 feature_id，或明确指定当前 output_feature 时，均以最终 B-Rep 实测并在 STEP 重导入后复验；指定中间特征的这类约束仍标记 unverified，避免用最终实体替代中间结果伪造证据。

仅验证 output_feature 可达的几何依赖；无关分支中生成过一个圆角不能证明最终输出含该圆角。圆角被后续 Cut 完全删除会失败。下游 Transform/Pattern 改变孔坐标后，须提供完整的最终 expected_geometry.holes，避免用旧坐标伪造通过。复杂后续变换后的圆角/倒角表面追踪目前返回 unverified；任意自定义 feature.validation_conditions、没有注册几何验证器的对称/距离/制造约束同样明确标记 unverified。

直边圆角和对称直边倒角已真实验证。复杂自由曲面倒角/圆角若无法取得可靠尺寸证据，不会标记完成。几何核成功不代表符合公差、表面粗糙度、材料强度或可制造性要求。

## 执行与资源

Web 服务通过 `CADExecutor.execute(ir, output_dir, cancel_event, on_stage)` 启动独立 Python 进程。子进程重新校验并编译 IR，执行受信任的操作映射；不接受也不运行模型生成的任意 Python。`model.py` 是固定编译器生成的 IR 重放入口。

默认约束：单服务进程并发 2、地址空间 4 GiB、单文件 128 MiB、打开文件描述符 128。`CADExecutor` 直接实例化时墙钟预算为 60 秒；应用服务通过 `CAD_V2_TIMEOUT_SECONDS` 配置，默认 90 秒。CPU 软预算取墙钟预算向上取整，硬预算再加 1 秒。Python 路径可以用 `CAD_V2_PYTHON` 指定。Linux `resource` 和进程组用于预算及超时/取消终止。状态 building、validating、exporting 来自子进程实际阶段消息，不以计时动画模拟。

输入、导出和临时目录按任务隔离。非空输出目录拒绝覆盖；失败、超时、取消会清理本次目录；有效历史版本由上层版本库保留。子进程环境使用白名单，不继承模型 API Key、代理凭据或其他服务密钥。

默认 `CAD_V2_ISOLATION=strict`：`sandbox.py` 要求 Linux x86_64/aarch64、Landlock ABI ≥ 3 和 libseccomp；限制文件内容访问为运行时/应用只读路径和本次任务目录，默认拒绝未允许的系统调用，包括网络 socket、exec 和跨进程操作。不可用时返回 `IsolationUnavailableError`，不静默降级。`process-dev` 是显式开发退出项，仅保留子进程与资源预算；报告的 `isolation.os_enforced` 区分两者。此策略面向受信任 IR 编译器，不是任意上传 Python 的执行承诺，也不宣称隐藏宿主文件元数据、CPU 侧信道或实现容器/网络命名空间隔离。

## 产物一致性

同一 worker 任务从同一通过验证的最终 B-Rep 写出 `model.step`、`model.stl`、`model.glb`、`drawing.dxf` 和 `drawing.svg`，保存 `model.ir.json` 和 `model.py`，返回相对文件名、SHA-256 和字节数。上层以不可变 Document Revision 目录和 manifest 关联这组文件；二维图与三维预览都按同一 revision 下载。`Build123dBackend.export` 本身负责三维文件，worker 随后调用 `export_drawings`；二维导出失败同样使本次任务失败，不发布部分产物。

GLB 遵循 glTF 单位与坐标：米、Y-up；内核数据是 mm、Z-up，report.geometry.preview 明确标记转换。Viewer 可加载完整 GLB 场景后整体乘 1000、绕 X 轴 +90° 恢复 CAD 显示坐标。GLB 具有真实网格法线及蓝灰材质。后端 tessellate API 本身则保持 mm、Z-up。

## 真实三视图与尺寸

`drawings.export_drawings` 使用 OpenCascade `HLRBRep_Algo` 对最终精确 B-Rep 做正投影和消隐，不读取 GLB/STL 网格，也不将三维预览截图冒充工程图。采用第三角投影、1:1 毫米坐标：

| 视图 | 观察侧 / 图纸横纵轴 | 布局 |
|---|---|---|
| FRONT | 从 −Y 侧观察；X/Z | 左下 |
| TOP | 从 +Z 侧观察；X/Y | 正视图上方、X 对齐 |
| RIGHT | 从 +X 侧观察；Y/Z | 正视图右侧、Z 对齐 |

DXF 包含 `VISIBLE`、`HIDDEN`、`CENTER`、`DIMENSIONS`、`ANNOTATIONS` 图层；隐藏线使用虚线，识别到的孔轴使用中心线。相同投影曲线去重，共线线段优先保留可见轮廓，避免将方盒背面的重合边画成虚线。线、圆、圆弧、椭圆及 NURBS 使用原生 DXF 实体；NURBS 保留内核曲线的次数、节点和权重，不统一离散成折线。DXF 设为毫米并进行结构审计。

尺寸证据来自最终几何，而非复制 IR 参数：

- 总长、总宽、总高使用 `BRepBndLib.AddOptimal` 的无网格包围盒。
- 孔径和孔轴坐标来自内向、完整 2π 圆柱壁；仅在标准视图中存在匹配的可见圆孔轮廓时标注孔径和基准位置。同视图相同孔径可合并为数量标注。
- DXF 尺寸使用可编辑的原生 `DIMENSION` 实体，测量比例为 1；不是仅写一段尺寸文字。SVG 使用同一投影及测量数据生成预览。

`report.geometry.drawing` 记录 `method=exact_brep_hlr`、单位、投影制式、包围盒、各视图基向量和偏移、线型数量、孔测量及尺寸来源。SVG 的直线/圆/圆弧采用对应矢量原语；椭圆及样条预览按 0.01 mm 偏差目标采样，每条曲线最多 8192 点，超过预算报错。报告分别列出 `svg_curve_deflection_mm` 和各视图 `svg_approximated_curves`；精确 B-Rep 消隐不等于这些 SVG 预览路径也完全解析。

当前是自动生成的基本三视图和受支持尺寸，不保证完整制造图。尚无自动剖视图、公差/GD&T、螺纹或表面粗糙度标注；不推断孔深、贯通性或配合等级。斜孔、被遮挡或非完整圆柱孔轮廓可能只投影几何而不自动标注。CENTER 当前仅依据识别的孔轴生成，尚未为外圆柱或自由曲面自动生成中心线。无厚度面片、空形体和无有效实体输入被拒绝。

## 有界拓扑恢复

`healing.py` 提供三个候选策略，在原形体的深拷贝上运行；这不是“任意坏几何都能修复”的保证。

| 策略 | 实际操作与边界 |
|---|---|
| `shape_fix` | `ShapeFix_Shape` 修复受限容差/参数/方向问题；关闭顶点位置修正和开放实体创建，不要求填孔或删除特征 |
| `orient_solids` | `BRepLib.OrientClosedSolid` 修正已有封闭实体的方向；不接受开放实体，也不丢弃实体容器外的面 |
| `sew_closed_shells` | 在受限容差内缝合现有面，再由闭壳恢复实体；拒绝自由边、非流形边、被删面及开放壳，不补造缺失面 |

可信执行策略的容差范围为 `1e-7–1e-5 mm`，不从模型 IR metadata 读取。应用策略按 `shape_fix`、`orient_solids`、`sew_closed_shells` 顺序尝试未重复候选，仅对 worker 标记 `healing_candidate=true` 的可恢复 `InvalidTopologyError` 启用。抛异常而没有候选形体的操作、验证约束不满足、基础设施故障不因此获得改动设计的权限。

每个候选必须通过有限数值、有效拓扑、所需正体积、绝对体积/面积/包围盒、已有实体数量、面数量与几何签名（含内环数量和支持曲面的半径）保持检查，以及拓扑容差上限检查。没有实体容器时，只有闭壳缝合可恢复实体；之后仍须满足原始 IR 的实体数量、孔、尺寸等要求和真实 STEP 写出/重导入验证。`accepted` 的 healing 证据本身不等于任务可以发布。

成功 trace 保存策略、容差、前后测量、不变量检查和耗时；失败保留 rejected 证据和原因。自动修复历史还保存候选 fingerprint，默认共享最多 3 次修复预算（可配 0–5），不反复尝试同一候选。原有 Fillet/Chamfer 语义选择精度修复仍单独保留：必须得到预期数量的唯一集合，只改变定位容差，策略上限 0.001 mm；此上限不是拓扑 healing 的容差上限，也不改变圆角半径、孔位或实体尺寸。

## 可复现验证

```sh
cd backend
API_KEY=test PYTHONPATH=. python -m pytest tests/cad_v2/test_kernel.py tests/cad_v2/test_executor.py tests/cad_v2/test_drawings.py tests/cad_v2/test_healing.py -q
```

`test_drawings.py` 通过独立 ezdxf 读取检查毫米单位、原生尺寸值、孔中心/半径、隐藏孔壁、三视图布局与非对称台阶零件；另覆盖平移、侧孔、斜圆柱椭圆、球轮廓、圆环 NURBS 和 SVG 转义/确定性。`test_healing.py` 用真实 OCP 顶点偏差、反向实体和拆散面构造缺陷，检查三个恢复策略及原始孔/约束、STEP 重导入；同时验证缺面、超界缺陷、空腔变材料和不满足设计约束的候选被拒绝。人为注入的缺陷用于验证恢复边界，不能计作任意真实零件的自动修复成功率。

内核和执行器测试仍涵盖语义选择歧义、真实 STEP 重导入、GLB 材质/法线、孔数/盲孔深度、被删除圆角、取消/超时、失败清理和可重放 Python。日常测试不调用付费模型；具体运行结果按本次测试日志记录，不将文件中用例数量当作已执行结果。
