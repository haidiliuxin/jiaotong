"""Keep dataset claims tied to the selected input source."""
import re
from pathlib import Path

from .runner import DEFAULT_SOURCE


def is_default_source(source=None):
    return source is None or Path(source).resolve() == DEFAULT_SOURCE.resolve()


def source_label(source):
    path = Path(source)
    raw = path.parent.name if path.name == "基础路网" else path.name
    return re.sub(r"[^\w\u4e00-\u9fff（）() -]", " ", raw).strip()[:80] or "未命名输入案例"


def source_description(source):
    label = source_label(source)
    if "examples" in Path(source).parts and re.fullmatch(r"教学案例\d+", label):
        return f"软件自带{label}"
    return label


def display_dataset(source=None):
    return "南京赛事案例" if is_default_source(source) else source_description(source)


def assumption(source=None):
    if is_default_source(source):
        return "案例基准需求时段，1小时窗口，非南京实测早高峰"
    return "输入案例基准需求时段，1小时窗口；不代表现实实测早高峰"


def report_notice(source=None):
    if is_default_source(source):
        return "赛事南京案例已做数据调整，以下结果不代表南京真实交通状况。"
    return f"输入案例：{source_description(source)}。以下结果仅对应该仿真数据，不代表现实交通状况。"


def recommendation_scope(source=None):
    if is_default_source(source):
        return "仅是赛事调整案例中的仿真建议，非现实实施决定"
    return "仅是所选输入案例中的仿真建议，非现实实施决定"


def footer_text(source=None):
    prefix = "赛事调整案例" if is_default_source(source) else f"输入案例：{source_description(source)}"
    return prefix + " · 非实时交通 · 无外部字体、脚本或服务依赖。浏览器打印可保存为PDF；正式交付前仍需检查排版。"
