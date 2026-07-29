---
name: fund-liquidation-clause
description: |
  公募基金清盘条款批量分类。三级递进流水线：Datayes完整合同→同合同宽松复判/替代公告源→CSRC多候选兜底，输出Excel含管理人汇总、逐只明细和统计说明。
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
- 基础锚点严格模式：20日、200人和5000万元使用阿拉伯/全角数字；处置期限同时识别“10/十日、6/六个月、50/五十日、60/六十日”等混合写法

### 阶段二：Datayes 宽松复判（阶段一未分类的基金）

1. **阶段二A：同合同宽松复判**
   - 重新使用阶段一选中的完整基金合同
   - 宽松模式增加中文数字和 PDF 页眉插入容错
2. **阶段二B：替代公告源**
   - 只处理阶段二A仍未分类的基金
   - 优先级：招募说明书 > 发售公告 > 成立公告 > 资料概要 > 发行运作

引擎：PyMuPDF；输出乱码时自动切换 pypdf。

### 阶段三：CSRC 证监会（阶段二仍未分类的基金）

- 数据源：CSRC 信息披露平台 `advanced_search_report.do`
  - `reportType=FA020010`（基金合同）→ 无结果或候选耗尽后尝试 `FA010010`（招募说明书）
  - 查询范围从 `2000-01-01` 开始，每页 50 条并自动分页
  - 完整合同、修订版优先；费率调整公告、修改公告和摘要降权
  - 每个候选依次执行下载、PDF 校验、解析和分类，单个候选失败后继续下一份
  - 已知系列基金 `151002` 的系列合同按 CSRC 主索引代码 `151001` 兜底，并只接受标题含“系列”的文档
- 引擎：**仅 pypdf**（CSRC PDF 的 SimSun 字体 CMap 不标准，PyMuPDF容易乱码）
- 最宽松模式：增加 OCR 分隔符、旧契约 100 人门槛和“基金契约”表述容错
## 三种分类类型

| 类型 | 触发条件 | 关键词 |
|---|---|---|
| **类型1: 备案** | 向证监会报告并提交解决方案，但未同时出现“10日报告+6个月大会”，且不直接终止 | `报告/说明原因` + `解决方案/备案` |
| **类型2: 备案+6个月大会** | 50日或60日后的同一处置链同时包含10日内报告和6个月内召集大会 | `10个工作日内报告` + `6个月内召集持有人大会` |
| **类型3: 自动触发终止** | 50日或60日后直接终止/清算且无需大会；旧契约允许100人门槛和监管批准后终止 | `终止/清算` + `无需大会`，或旧契约直接终止 |

支持阿拉伯数字、中文数字、全角数字和 PDF 页眉插入容错。现代合同以同一句中的20个工作日、200人、5000万元为基础锚点；阶段三兼容2004年前后旧契约的100人门槛和“基金持有人”表述。
## 运行

```bash
python3 -X utf8 <skill_root>/scripts/pipeline.py "{用户输入的Excel路径}"
```

可选参数：
- `--output <路径>`：指定输出 Excel 路径（默认：`{输入目录}/清盘条款分类.xlsx`）
- `--work-dir <目录>`：工作目录（保存 PDF 缓存和中间 JSON）
- `--skip-stage3`：跳过 CSRC 阶段（无网络/限流时可用）
- `--no-resume`：忽略已有结果缓存，从头重新处理全部基金
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

同时输出版本化的 `results_cache.json`。脚本默认自动复用当前流水线版本中已成功分类的基金；失败记录会继续重试。每 25 条处理结果及每个阶段结束时原子保存检查点，失败项保留 `failureHistory`，CSRC 内部保留 `candidateAttempts`。旧版无结构缓存或不同流水线版本的缓存不会自动复用。

## 分类逻辑

```python
# 定位: 现代合同以20日+200人+5000万元为同句锚点；stage3兼容旧契约100人门槛
# 阶段: stage1基础门槛严格、处置期限允许中阿混写；stage2基础门槛增加中文数字/页眉容错；stage3增加 OCR/旧契约容错
# 判定优先级:
if (50日 or 60日) and 终止/清算 and (无需大会 or 旧契约直接终止):
    类型3
elif (50日 or 60日) and 10日内报告 and 6个月内大会:
    类型2
elif 报告证监会 and (解决方案 or 备案 or 大会):
    类型1
```

## 注意事项

- 阶段三默认 8 worker 并行，并在入口再次排除已有成功结果；若遇到 CSRC 限流，可临时调低 `scripts/csrc_api.py` 中的 `CSRC_WORKERS`
- PDF 下载使用 URL 级 MD5 文件缓存；同一轮流水线中的相同 URL 只下载、解析和分类一次
- 阶段一/二使用独立流式线程池：API 16 worker、下载 16 worker、PDF 解析 8 worker，上游完成后立即进入下一环节
- A/C 份额可能共用同一份合同，链接可能相同
- 发起式基金叠加"成立满3年+净值<2亿自动终止"条款，本次以标准存续期条款为准

## 跨平台运行

- Windows 建议始终使用 `python -X utf8`，避免系统 GBK 默认编码影响中文日志、JSON 和路径；macOS/Linux 使用 `python3 -X utf8`。
- CSRC 阶段在 Windows 优先调用 PowerShell，其他系统或 PowerShell 失败时自动切换到 Python `urllib`。
- Excel 使用“微软雅黑”显示中文；非 Windows 系统未安装该字体时，Excel 软件会使用本地可用中文字体替代，不影响数据内容。
## 执行约束

- 禁止网页搜索：本 Skill 只使用已声明的 Datayes API、Datayes S3 PDF 和 CSRC 信息披露平台，不使用 WebSearch/WebFetch
- Host 白名单：脚本请求前校验目标域名，允许 `gw.datayes.com`、`r.datayes.com`、`bigdata-s3.wmcloud.com`、`eid.csrc.gov.cn`
- 减少重复请求：同一 PDF URL 按 MD5 文件名缓存，同一轮运行内复用相同 URL 的下载、解析和分类结果
- 断点续跑：仅复用 `schemaVersion=3` 且 `pipelineVersion=0.2.0` 的成功结果；失败项会重新处理并保留逐阶段原因
- CSRC 输入约束：阶段三只允许接收阶段一、阶段二A、阶段二B及缓存均未成功分类的基金
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
