from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def png_to_pdf(png_path: Path) -> Path | None:
    pdf_path = png_path.with_suffix(".pdf")
    if pdf_path.exists() and pdf_path.stat().st_mtime >= png_path.stat().st_mtime:
        return None
    with Image.open(png_path) as image:
        rgb = image.convert("RGB")
        rgb.save(pdf_path, "PDF", resolution=300.0)
    return pdf_path


def main() -> int:
    outputs = ROOT / "outputs"
    written: list[Path] = []
    for png_path in outputs.rglob("*.png"):
        out = png_to_pdf(png_path)
        if out is not None:
            written.append(out)
    print("\n".join(str(path) for path in written) if written else "all png files already have pdf companions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
