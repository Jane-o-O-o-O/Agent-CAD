# 几何执行、验证与交付边界

实现：`backend/app/infrastructure/cad_v2/backend.py`、`validation.py`、`worker.py`、`executor.py`。领域契约与静态校验位于 `backend/app/domain/cad_v2/`。已锁定并实际测试 build123d 0.11.1 / OpenCascade OCP 7.9.3.1.1；建模结果是精确 B-Rep，STL/GLB 仅作为其派生产物。

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

仅验证 output_feature 可达的几何依赖；无关分支中生成过一个圆角不能证明最终输出含该圆角。圆角被后续 Cut 完全删除会失败。下游 Transform/Pattern 改变孔坐标后，须提供完整的最终 expected_geometry.holes，避免用旧坐标伪造通过。复杂后续变换后的圆角/倒角表面追踪目前返回 unverified；任意自定义 feature.validation_conditions、没有注册几何验证器的对称/距离/制造约束同样明确标记 unverified。

直边圆角和对称直边倒角已真实验证。复杂自由曲面倒角/圆角若无法取得可靠尺寸证据，不会标记完成。几何核成功不代表符合公差、表面粗糙度、材料强度或可制造性要求。

## 执行与资源

Web 服务通过 `CADExecutor.execute(ir, output_dir, cancel_event, on_stage)` 启动独立 Python 进程。子进程重新校验并编译 IR，执行受信任的操作映射；不接受也不运行模型生成的任意 Python。`model.py` 是固定编译器生成的 IR 重放入口。

默认约束：单服务进程并发 2、墙钟 60 秒、CPU 60 秒、地址空间 4 GiB、单文件 128 MiB、打开文件描述符 128。Python 路径可以用 `CAD_V2_PYTHON` 指定。Linux `resource` 和进程组用于预算及超时/取消终止。状态 building、validating、exporting 来自子进程实际阶段消息，不以计时动画模拟。

输入、导出和临时目录按任务隔离。非空输出目录拒绝覆盖；失败、超时、取消会清理本次目录；有效历史版本由上层版本库保留。子进程环境使用白名单，不继承模型 API Key、代理凭据或其他服务密钥。

这是**受信任操作执行器和资源隔离**，不宣称完整 OS 安全沙箱。当前环境未验证容器/网络命名空间隔离；子进程仍受宿主操作系统用户权限约束。生产可进一步使用低权限容器、只读根目录、无网络、seccomp/cgroup。任何未来“运行任意 Python”能力必须另行提供真正安全隔离，不能复用当前进程执行器作为安全证明。

## 产物一致性

同一任务从同一 B-Rep 写出 `model.step`、`model.stl`、`model.glb`，保存 `model.ir.json` 和 `model.py`，返回相对文件名、SHA-256 和字节数。上层以 Document Revision 关联这组文件。

GLB 遵循 glTF 单位与坐标：米、Y-up；内核数据是 mm、Z-up，report.geometry.preview 明确标记转换。Viewer 可加载完整 GLB 场景后整体乘 1000、绕 X 轴 +90° 恢复 CAD 显示坐标。GLB 具有真实网格法线及蓝灰材质。后端 tessellate API 本身则保持 mm、Z-up。

## 可复现验证

```sh
cd backend
API_KEY=test PYTHONPATH=. python -m pytest tests/cad_v2/test_kernel.py tests/cad_v2/test_executor.py -q
```

2026-10-14 本轮实跑该组 **37 项通过**（30 个几何/导出检查测试和 7 个独立子进程测试），16.87 秒。测试执行真实内核和真实子进程，涵盖所有已列特征、非零 YZ 草图原点、真实 STEP 重导入、GLB 材质/法线、错误孔数、孔被堵、盲孔深度、圆角被删除、语义选择歧义、参数不变的精度修复、取消/超时、失败清理和可重放 Python。日常测试不调用付费模型。

可恢复场景：边中心输入与实际值相差 0.00005 mm，而选择容差仅 0.000001 mm 时，实际选择失败；仅在放宽到不超过 0.0001 mm 后获得准确预期数量的唯一集合时，内核建议精度修复。修复不会修改圆角半径、实体尺寸、孔数或孔位置；上层有次数与重复尝试限制。选择多个错误候选不会采用“选第一个”作为修复。
