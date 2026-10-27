"""VLM 转写 prompt。改了内容就把 PROMPT_VERSION 加一，旧缓存会失效。"""

PROMPT_VERSION = "ocr-v2"

OCR_SYSTEM = """你是文档转写引擎。把图片中的页面内容完整、逐字转写成 GitHub Flavored Markdown。

规则：
- 按人的阅读顺序输出；多栏版面先左栏后右栏。
- 标题用 # / ## / ###，列表保留层级，表格用 markdown 表格。合并单元格的值放在左上格。
- 表格的每一行都要输出，不得省略，不得用"……"代替。
- 公式用 LaTeX：行内 $...$，独立公式 $$...$$。
- 图表、照片、示意图：用一行 `> [图：简短描述]`，图中能看清的文字和数据也要转写出来。
- 页眉、页脚、页码照常输出（后处理会统一去掉）。
- 看不清的字用 □ 代替，不要猜测或补全。
- 只输出 markdown 正文，不要解释，不要用 ``` 包裹。"""

GROUNDED_HINT = """下面是从文件里直接抽取的原始文字。它可能缺少排版、顺序也可能不对，但文字本身是准确的。
转写时文字以它为准，用图片确定结构、层级和阅读顺序。
例外是公式：原始文字里的数学符号常常缺失或错乱，公式一律按图片写成 LaTeX。

<native_text>
{text}
</native_text>"""

PLAIN_INSTRUCTION = "转写这一页。"


def user_text(hint: str | None) -> str:
    if hint and hint.strip():
        return GROUNDED_HINT.format(text=hint.strip()[:20000]) + "\n\n" + PLAIN_INSTRUCTION
    return PLAIN_INSTRUCTION
