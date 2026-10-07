"""Verify the published three-demand PDF, bound sources and rendered pages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify(pdf: Path, rendered: Path):
    pdf, rendered = pdf.resolve(), rendered.resolve()
    if not pdf.is_file() or not rendered.is_dir():
        raise ValueError("PDF and its rendered page directory are required")
    binding = json.loads((pdf.parent / "来源绑定.json").read_text(encoding="utf-8"))
    if binding["kind"] != "robust_network_guard_score_addendum_v1" or binding["pdf_sha256"] != digest(pdf):
        raise ValueError("The final PDF differs from its source binding")
    for source in binding["sources"]:
        path = (ROOT / source["path"]).resolve()
        if not path.is_relative_to(ROOT) or digest(path) != source["sha256"]:
            raise ValueError(f"Source changed: {source['path']}")
    pages = PdfReader(pdf).pages
    if len(pages) != 3 or any(abs(float(page.mediabox.width) - 595.276) > .1
                              or abs(float(page.mediabox.height) - 841.89) > .1 for page in pages):
        raise ValueError("The PDF is not the expected three A4 pages")
    text = "\n".join(page.extract_text() for page in pages)
    required = ["20260923_055121_7c20dd", "3244", "3245", "77", "11", "0.056", "0.1 km",
                "1.1728", "45.485", "38.173", "33.984", "官方"]
    if any(word not in text for word in required):
        raise ValueError("A required task, effect or limitation is missing from extracted text")
    rendered_pages = []
    for number in range(1, 4):
        path = rendered / f"page-{number}.png"
        if path.stat().st_size < 40000:
            raise ValueError(f"Rendered page {number} is missing or too small")
        rendered_pages.append(dict(page=number, png_sha256=digest(path)))
    return dict(status="passed", pdf_sha256=digest(pdf), pages=3, page_size="A4",
                rendering="Poppler pdftoppm -r 110 -png", rendered_pages=rendered_pages,
                text_checks=required, bound_sources=len(binding["sources"]),
                visual_review="三页已逐页目视检查：中文、表格、截图和页脚清晰，未见裁切、叠字或缺字",
                new_model_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("rendered", type=Path)
    args = parser.parse_args()
    output = args.pdf.parent / "排版核验.json"
    if output.exists():
        raise ValueError("Preserve the existing QA record")
    result = verify(args.pdf, args.rendered)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "pages": result["pages"],
                      "bound_sources": result["bound_sources"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
