# Agent-CAD

GitHub：<https://github.com/Jane-o-O-o-O/Agent-CAD>

Agent-CAD 是面向 CAD 工程工作流的 AI Agent 系统，支持参数化三维建模、二维 DXF 图纸、几何校验和工程文件导出。项目采用 FastAPI、AgentScope 与 Vue，提供可私有部署的会话、沙箱、工具和实时事件基础设施。

## 核心功能

 * **CAD 工作台：**参数化零件和装配体建模、三维预览、参数与特征编辑、版本管理。
 * **校验与导出：**几何测量、碰撞检查，以及 STEP、STL、GLB、DXF、SVG 等文件导出。
 * **工具：**Shell、Browser、File、Web Search 与外部 MCP 工具集成。
 * **Claw：**集成 [OpenClaw](https://github.com/anthropics/openclaw) AI 助手，提供用户隔离容器、自动过期与聊天历史。
 * **任务会话：**通过 MongoDB 和 Redis 管理会话历史与后台任务。
 * **交互：**支持文件上传与下载、任务停止与打断、中文与英文界面、用户认证。

## 文档

 * [快速上手](quick_start.md)
 * [配置说明](configuration.md)
 * [系统架构](architecture.md)
 * [CAD 技术设计](agent-cad-technical-design.md)
