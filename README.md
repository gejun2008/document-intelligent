# docintel：文档 → Markdown 流水线

原生解析优先，视觉模型兜底；按页并发、分引擎限流；页级缓存；每页都有质量分。

## 路由

| 输入 | 主路径 | 增强 / 兜底 |
|---|---|---|
| docx | mammoth → HTML → markdown（标题、列表、表格） | 内嵌图片 → VLM 回填占位符；几乎全是图片的 docx → LibreOffice → PDF 路由 |
| xlsx | openpyxl 读单元格（合并单元格、隐藏 sheet、大表分块） | 内嵌图片 → VLM |
| pptx | python-pptx 抽文本、表格、备注 | 有 LibreOffice 时渲染幻灯片 → grounded VLM（原生文本作提示） |
| pdf | 逐页：文本层可用 → pymupdf4llm | 扫描 / 乱码 / 叠旧 OCR 层的页 → Azure DI prebuilt-layout；图表页、公式页、**表格页** → grounded VLM |
| doc/xls/ppt/odt… | LibreOffice 先升级成 OOXML | 同上 |
| 图片 | VLM | — |

PDF 表格页一律不信文本层：文本层只有文字块和线条，pymupdf4llm 靠位置猜行列，olmOCR 表格组只有 71%。
检测方法是框线（`find_tables`，至少 2 行 2 列）加兜底（pymupdf4llm 抽出了 markdown 表格的页也改道）。
`routing.table_route` 控制去向：`vlm`（默认，看图定结构、文字以文本层为准）、`azure`（文本层留作质检参考）、`text`（不改道）。
olmOCR 上走文本层的表格页有 91% 被改道；其他组 10–16% 的页因含表格被改道，抽查都是真表格。

## 质量

- **线上（无参考）**：`quality/checks.py`：截断、复读死循环、表格列数、乱码、拒答、OCR 置信度。
- **原生文本当参考**：`quality/reference.py`。grounded VLM 页用词袋算覆盖率（漏字）、精确率（幻觉）、数字一致率。
- **页级分数** = 各维度的最小值。`pass` 直接通过；未通过时换引擎重跑一次（`vlm_strong`，或 OCR 失败的页换 VLM）；仍未通过的页写进 `out/review.jsonl`。
- **LLM-as-judge**（可选）：`quality/judge.py`，按 `judge_sample_rate` 抽样。
- **离线评测**：`docintel bench --pred out/ --gold gold/`，计算 NED、表格单元格 F1、标题 F1。

## 并发

- 文档级用 `max_docs_in_flight` 做背压。
- CPU 活（解析、渲染、切 PDF）放进程池。
- LibreOffice 池中每个实例使用独立 profile，带超时，超时后杀进程组。
- 每个引擎各自配并发信号量、RPM/TPM 令牌桶、指数退避（遵守 retry-after）和熔断器。
- Azure 把同一文档待 OCR 的页切成小 PDF，一次提交。
- 缓存 key = 文件哈希 + 页 + 引擎版本 + prompt 版本，重跑时就是断点续跑。

## 合成测试集

```bash
.venv/bin/docintel synth -o testset -n 9      # 每类 9 份，难度轮换：docx/xlsx/pptx/原生 PDF/扫描 PDF
.venv/bin/docintel run testset/docs -o out_ts -c config.toml
.venv/bin/docintel bench --pred out_ts --gold testset/gold   # 按 kind、难度分组汇总
```

内容先生成，金标 md 和文档同时产出，所以金标零成本。约定写在 `docintel/synth.py` 开头。
同一份内容既有原生 PDF 版也有扫描 PDF 版（加了灰度、旋转、模糊、噪点和 JPEG 压缩），
两者的分差就是视觉路径的损失。扫描版需要 Azure 或 VLM 凭据；其他类型不调模型，本地就能跑。

## 公开评测集

数据放在 `datasets/`，适配代码在 `docintel/benchmarks/`。

- olmOCR-Bench 原样放在仓库里，PDF 走 Git LFS（克隆前先装 git-lfs）。来源、许可和版本见 `datasets/olmocr-bench/NOTICE.md`。
- OmniDocBench 只限研究用途，仓库不再分发，需要自己下载，固定到评测用过的版本：
```bash
.venv/bin/hf download opendatalab/OmniDocBench --repo-type dataset --revision aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec --local-dir datasets/omnidocbench
```

**olmOCR-Bench**（allenai/olmOCR-bench，ODC-BY-1.0）：英文 PDF 单页，断言式单元测试。
```bash
.venv/bin/docintel olmocr prepare -o eval/olmocr [--suites multi_column old_scans] [--limit 100]
.venv/bin/docintel run eval/olmocr/docs -o out_olm -c config.toml
.venv/bin/docintel olmocr score --pred out_olm [--only-present] --failures out_olm/failures.jsonl
```
打分规则照官方 `olmocr/bench/tests.py` 复刻。math 测试官方会用 KaTeX 渲染后比较，这里只做字符串比较，所以 math 结果是下界。

**OmniDocBench**（opendatalab/OmniDocBench，仅限研究用途，不可商用）：中英文页面图，按版面标注转成金标 md。
```bash
.venv/bin/docintel omnidoc prepare -o eval/omnidoc [--per-source 20] [--lang zh mixed]
.venv/bin/docintel run eval/omnidoc/docs -o out_omni -c config.toml
.venv/bin/docintel bench --pred out_omni --gold eval/omnidoc/gold    # 按语言、文档来源分组
```
这是近似口径。要和论文、榜单对齐，请用官方评测脚本。

## 使用

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev,bench]"
cp config.example.toml config.toml   # 改 VLM 地址；密钥放环境变量
.venv/bin/docintel -v route docs/                 # 只看路由，不调模型
.venv/bin/docintel run docs/ -c config.toml -o out/
.venv/bin/pytest -q
```

每个输入产出 `out/<名字>.md` 和 `out/<名字>.quality.json`（逐页的路由、引擎、分数、问题、耗时、用量）。

## 扩展点

- 新模型：实现 `adapters/base.py` 里的 `VLMEngine` 或 `LayoutEngine`，在 `adapters/__init__.py` 注册。
- 改 prompt：改 `adapters/prompts.py`，同时把 `PROMPT_VERSION` 加一，旧缓存会自动失效。
- 分布式：`Pipeline.process(path)` 本身是自包含的单文档任务，可以直接放进 Celery、Ray 或 Temporal 的 worker。
