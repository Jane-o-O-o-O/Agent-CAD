# 数据处理模块 Agent Scope

这个模块可以作为项目主 Agent 的工具使用，但不建议作为项目主 Agent。

## 定位

```text
项目主 Agent
  -> 调用 Data Processor Service
      -> 文件解析
      -> 数据规范化
      -> 返回 JSON
```

## 允许做的事情

- 接收上传文件。
- 检查文件大小和扩展名白名单。
- 按固定工作流解析文件。
- 把文件内容转成统一 JSON。
- 提取 Word 里的文字、表格和图片基本信息，支持 `.docx`，并可在有转换器时支持 `.doc`。
- 把 Word 内图片也转换为 JSON 分析结构，包括尺寸、OCR 文本、结构化字段和错误原因。
- 返回解析结果给主后端或主 Agent。

## 不允许做的事情

- 不做项目整体任务规划。
- 不调用项目主 Agent。
- 不直接修改业务数据库。
- 不执行上传文件里的宏、脚本或外部链接。
- 不处理未知格式。
- 不处理压缩包。

## 当前固定工作流

```text
txt / md  -> 文本
html      -> 去除脚本和样式后的文本
json      -> 规范化 JSON
csv       -> records
xlsx/xls  -> sheets + records
doc       -> 先转 docx，再提取文本 + 表格 + 图片基本信息
docx      -> 文本 + 表格 + 图片基本信息
pdf       -> 文本
```

## 和 AgentScope 的关系

AgentScope 已经下载在：

```text
E:\A_ai_project\houduan\agentscope
```

当前数据处理服务没有直接改 AgentScope 源码，而是作为旁路服务存在：

```text
E:\A_ai_project\houduan\data_processor_service
```

这样做的好处是：

- AgentScope 框架保持干净，后续方便更新。
- 数据处理接口独立，主后端更容易接入。
- 不会和项目主 Agent 发生职责冲突。
- 后续如果需要，可以再把 `/data/parse` 包装成 AgentScope tool。

## 推荐对接方式

主后端调用：

```text
POST http://数据处理服务地址/data/parse
```

上传字段：

```text
file: 文件
include_raw: true/false
```

主 Agent 拿到返回 JSON 后，再决定是否继续做字段抽取、入库、检索或报告生成。

## `.doc` 说明

`.doc` 是老版 Word 二进制格式，不能像 `.docx` 一样直接解析。当前实现会先调用本机转换器转成 `.docx`：

```text
服务器推荐：LibreOffice / soffice
Windows 本地可用：Microsoft Word + pywin32
```

如果部署服务器没有这些转换器，`.doc` 会返回明确错误；`.docx` 不受影响。

`.doc` 成功解析后，返回的 `metadata` 会包含：

```json
{
  "image_count": 6,
  "word_conversion": "doc_to_docx"
}
```

## 图片内容 JSON

每张图片都会包含 `analysis` 字段：

```json
{
  "filename": "image1.emf",
  "content_type": "image/emf",
  "analysis": {
    "status": "unsupported",
    "format": "emf",
    "width": null,
    "height": null,
    "ocr_text": "",
    "structured_data": {},
    "errors": ["当前环境缺少 EMF 转 PNG 或 OCR 工具"]
  }
}
```

如果图片是 PNG/JPG 等普通图片，会优先返回尺寸信息；如果安装了 Tesseract，会继续返回 `ocr_text`。

不返回图片二进制内容，只返回 OCR 和结构化分析结果，避免响应体过大。
