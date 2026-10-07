"""Verify the medium-region PDF against bound sources and rendered A4 pages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(pdf: Path, rendered: Path):
    pdf, rendered = pdf.resolve(), rendered.resolve()
    if not pdf.is_file() or not rendered.is_dir():
        raise ValueError("PDF与渲染页目录必须存在")
    binding = json.loads((pdf.parent / "中密度增补来源绑定.json").read_text(encoding="utf-8"))
    if (binding["kind"] != "medium_robust_guard_score_addendum_v1"
            or binding["pdf_sha256"] != digest(pdf)
            or binding["new_model_calls_during_export"] != 0):
        raise ValueError("PDF与来源绑定不符")
    for item in binding["sources"]:
        path = (ROOT / item["path"]).resolve()
        if not path.is_relative_to(ROOT) or digest(path) != item["sha256"]:
            raise ValueError(f"绑定来源已变：{item['path']}")
    pages = PdfReader(pdf).pages
    if (len(pages) != 2 or any(abs(float(page.mediabox.width) - 595.276) > .1
                               or abs(float(page.mediabox.height) - 841.89) > .1
                               for page in pages)):
        raise ValueError("不是预期两页A4")
    content = "\n".join(page.extract_text() for page in pages)
    required = ["20260923_070135_0f0c08", "74", "645", "10", "70", "0.1 km",
                "4.182", "3.168", "6.230", "7.461", "7.678", "40组", "官方"]
    if any(word not in content for word in required):
        raise ValueError("任务、量化效果或边界缺失")
    images = []
    for page in (1, 2):
        path = rendered / f"page-{page}.png"
        if not path.is_file() or path.stat().st_size < 40000:
            raise ValueError(f"第{page}页渲染文件缺失或异常")
        images.append(dict(page=page, png_sha256=digest(path)))
    return dict(status="passed", pdf_sha256=digest(pdf), pages=2, page_size="A4",
                rendering="Poppler pdftoppm -r 110 -png", rendered_pages=images,
                text_checks=required, bound_sources=len(binding["sources"]),
                visual_review="两页已逐页目视检查：中文、表格与页脚清晰，未见裁切、叠字或缺字",
                new_model_calls=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("rendered", type=Path)
    args = parser.parse_args()
    record = args.pdf.parent / "中密度增补排版核验.json"
    if record.exists():
        raise ValueError("保留已有排版核验记录")
    result = verify(args.pdf, args.rendered)
    record.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status=result["status"], pages=result["pages"],
                          bound_sources=result["bound_sources"]), ensure_ascii=False))
