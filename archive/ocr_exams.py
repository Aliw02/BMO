"""
OCR script – extracts Arabic text from exam images and saves as .txt files
Usage: python ocr_exams.py
"""

import os
import sys
from pathlib import Path

try:
    import easyocr
except ImportError:
    print("Installing easyocr...")
    os.system(f"{sys.executable} -m pip install easyocr")
    import easyocr

BASE_DIR = Path(r"F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\data\downloads\اسئلة_السادس_العلمي_2025_دور_اول")
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

reader = None

def get_reader():
    global reader
    if reader is None:
        print("Loading EasyOCR (Arabic)...")
        reader = easyocr.Reader(["ar"], gpu=False)
    return reader

def extract_text(image_path: Path) -> str:
    r = get_reader()
    results = r.readtext(str(image_path), paragraph=True)
    lines = []
    for res in results:
        text = res[1].strip()
        if text:
            lines.append(text)
    return "\n".join(lines)

def process_subject(subject_dir: Path):
    images = sorted(
        [f for f in subject_dir.iterdir() if f.suffix.lower() in SUPPORTED_EXTS]
    )
    if not images:
        return 0

    subject_name = subject_dir.name[3:]
    txt_path = subject_dir / f"{subject_dir.name}.txt"
    print(f"\n{'='*50}")
    print(f"Subject: {subject_name}")
    print(f"{'='*50}")

    all_text = []
    count = 0
    for img_path in images:
        print(f"  OCR: {img_path.name}...", end=" ")
        try:
            text = extract_text(img_path)
            if text.strip():
                all_text.append(f"{'='*50}")
                all_text.append(f"Question {img_path.stem}")
                all_text.append(f"{'='*50}")
                all_text.append(text)
                all_text.append("")
            print(f"✓ ({len(text)} chars)")
            count += 1
        except Exception as e:
            print(f"✗ {str(e)[:50]}")

    if all_text:
        txt_path.write_text("\n".join(all_text), encoding="utf-8")
        print(f"\n  Saved: {txt_path.name} ({txt_path.stat().st_size // 1024}KB)")
    else:
        print(f"\n  No text extracted.")

    return count

def main():
    total_images = 0
    total_texts = 0

    subject_dirs = sorted(
        [d for d in BASE_DIR.iterdir() if d.is_dir() and d.name[:2].isdigit()]
    )

    print(f"Found {len(subject_dirs)} subjects in {BASE_DIR.name}\n")

    for sub_dir in subject_dirs:
        imgs = process_subject(sub_dir)
        if imgs > 0:
            total_images += imgs
            total_texts += 1

    print(f"\n{'='*50}")
    print(f"Done! {total_images} images OCRed across {total_texts} subjects")
    print(f"Text files saved in each subject folder")

if __name__ == "__main__":
    main()
