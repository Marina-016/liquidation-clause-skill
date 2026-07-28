---
name: fund-liquidation-clause
description: |
  公募基金清盘条款批量分类。三级递进流水线：Datayes基金合同→替代公告源→CSRC证监会兜底，输出Excel含管理人汇总、逐只明细和统计说明。
  当用户要批量分析基金清盘条款、清盘条款分类、基金合同第五部分条款提取时使用。不处理非公募基金、非清盘条款分类或非 Excel 批量输入任务。
  依赖 DATAYES_TOKEN 环境变量或 --token 参数，以及 python3 和 requirements.txt 中的 Python 包。
metadata: {"openclaw": {"requires": {"env": ["DATAYES_TOKEN"], "bins": ["python3"]}}, "short-description": "基金清盘条款批量分类"}
---

# 基金清盘条款分类

从基金合同中提取"第五部分 基金备案"中的清盘条款，自动分类为三种类型。

把 `<skill_root>` 视为当前 skill 根目录；脚本路径都相对 `<skill_root>` 解析。

## 前置条件

1. 用户提供基金列表 Excel（至少含基金代码列，建议含名称和管理人）
2. 通联数据 API Token（环境变量 `DATAYES_TOKEN`，或运行时通过 `--token` 传入）
3. Python 依赖：见 `requirements.txt`

## 输入

首先询问用户提供基金列表 Excel 文件。如果用户不清楚，引导其提供：

1. **直接拖入**：将 `.xlsx` 文件拖入对话窗口
2. **路径粘贴**：粘贴完整路径，如 `C:\Users\xxx\Desktop\基金列表.xlsx`
3. **相对路径**：如果文件在当前工作目录，直接写文件名

- **必需**：基金列表 Excel 文件路径
  - 列名自动识别：`基金代码`/`代码`/`fundCode`、`基金名称`/`名称`、`基金管理人`/`管理人`
  - 代码自动清洗：去 `.OF`/`.SZ`/`.SH` 后缀

## 三级流水线

只产出**一次** Excel，三个阶段的分类结果合并写入同一份文件。
每阶段只处理上一阶段**未成功分类**的基金。

### 阶段一：Datayes 基金合同（主源）

- 数据源：通联公告 API → `classifyName="基金合同"`
- 引擎：PyMuPDF（主力）
- 严格模式：阿拉伯数字（`20个工作日`/`200人`/`5000万`）

### 阶段二：替代公告源（阶段一未分类的基金）

- 优先级：招募说明书 > 发售公告 > 成立公告 > 资料概要 > 发行运作
- 引擎：PyMuPDF → 输出乱码则自动切换 pypdf
- 宽松模式：+中文数字（`二十`/`六十`/`五千万元`）

### 阶段三：CSRC 证监会（阶段二仍未分类的基金）

- 数据源：CSRC 信息披露平台 `advanced_search_report.do`
  - `reportType=FA020010`（基金合同）→ 无结果则 `FA010010`（招募说明书）
- 引擎：**仅 pypdf**（CSRC PDF 的 SimSun 字体 CMap 不标准，PyMuPDF 必然乱码，无需尝试）
- 最宽松模式：+放宽正则间距

## 三种分类类型

| 类型 | 触发条件 | 关键词 |
|---|---|---|
| **类型1: 备案** | 20日→披露, 60日→报告证监会+提方案+持有人大会 | `60个工作日` + `报告证监会` + `持有人大会`（无6月死线） |
| **类型2: 备案+6个月大会** | 同类型1链路上增加：10日内报告+6个月内召集大会 | `60个工作日` + `10个工作日` + `报告证监会` + `6个月内` + `持有人大会` |
| **类型3: 自动触发终止** | 50日→自动终止, 无需大会 | `50个工作日` + `自动终止` / `无需大会` |

支持阿拉伯数字和中文数字双模式。用"五千万元"锚点定位条款章节（避免误匹配募集成立条件中的"200人"）。

## 运行

```bash
python3 -X utf8 <skill_root>/scripts/pipeline.py "{用户输入的Excel路径}"
```

可选参数：
- `--output <路径>`：指定输出 Excel 路径（默认：`{输入目录}/清盘条款分类.xlsx`）
- `--work-dir <目录>`：工作目录（保存 PDF 缓存和中间 JSON）
- `--skip-stage3`：跳过 CSRC 阶段（无网络/限流时可用）
- `--token <token>`：Datayes API Token（优先读环境变量 `DATAYES_TOKEN`）

示例：
```bash
python3 -X utf8 <skill_root>/scripts/pipeline.py "C:\Users\xxx\基金列表.xlsx" --output 清盘条款分类.xlsx
```

## 输出

一个 `.xlsx` 文件，三张表：

| 表 | 内容 |
|---|---|
| `表1-管理人汇总` | 按管理人聚合：基金数量、三种类型数量及占比 |
| `表2-基金明细` | 逐只：代码、名称、管理人、分类、条款类型、条款原文、合同PDF链接、数据来源、分类阶段 |
| `表3-统计说明` | 分类标准、数据源说明、解析引擎说明、分阶段统计 |

同时输出 `results_cache.json`（保存本次中间结果，便于复核或后续人工复用；当前脚本不会自动读取旧缓存跳过已分类基金）。

## 分类逻辑

```python
# 定位: "五千万元" → 上下文含"份额持有人"/"二百人"
# 天数: 60/50/20 (阿拉伯+中文)
# 后续: 报告证监会 / 六个月大会 / 无需大会 / 自动终止
# 判定:
if (50日 or 60日) and (无需大会 or 自动终止) → 类型3
elif 60日 and (六个月内 or 10个工作日内)       → 类型2
elif 60日 and 报告证监会 and 无6月内时限        → 类型1
```

## 注意事项

- 阶段三默认 5 worker 并行；若遇到 CSRC 限流，可临时调低 `scripts/csrc_api.py` 中的 `CSRC_WORKERS`
- PDF 下载 URL 级 MD5 去重缓存（同 URL 只下一次）
- 阶段一/二并行度：`max(API_WORKERS, DL_WORKERS)`，默认 10 worker
- A/C 份额可能共用同一份合同，链接可能相同
- 发起式基金叠加"成立满3年+净值<2亿自动终止"条款，本次以标准存续期条款为准

## 执行约束

- 禁止网页搜索：本 Skill 只使用已声明的 Datayes API、Datayes S3 PDF 和 CSRC 信息披露平台，不使用 WebSearch/WebFetch
- Host 白名单：脚本请求前校验目标域名，允许 `gw.datayes.com`、`r.datayes.com`、`bigdata-s3.wmcloud.com`、`eid.csrc.gov.cn`
- 减少重复请求：同一 PDF URL 按 MD5 文件名缓存，同一轮运行内复用相同 URL 的分类结果
- 运行产物：输出 Excel、PDF 缓存和 `results_cache.json` 写入输入目录或 `--work-dir` 指定目录，不要求写入 skill 根目录

## Token

调用 Datayes API 前按顺序查找 token：

1. `DATAYES_TOKEN` 环境变量
2. `--token` 命令行参数

若都没有，提示用户提供通联数据 token。

### 获取 Datayes Token

访问 https://r.datayes.com/auth/token/login 获取 API token。

### 配置 Token

macOS / Linux:
```bash
export DATAYES_TOKEN='your-token'
```

Windows CMD:
```cmd
set DATAYES_TOKEN=your-token
```

Windows PowerShell:
```powershell
$env:DATAYES_TOKEN = "your-token"
```
