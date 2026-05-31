import requests, re, os
from bs4 import BeautifulSoup

headers = {"User-Agent": "Mozilla/5.0"}
base_dir = r"F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\data\downloads\اسئلة_السادس_العلمي_2025_دور_اول"
os.makedirs(base_dir, exist_ok=True)

all_subjects = {
    "01_التربية_الاسلامية": "https://www.amsebehm2017.com/2025/06/2025_0219470409.html",
    "02_اللغة_العربية": "https://www.amsebehm2017.com/2025/06/2025_0783141363.html",
    "03_اللغة_الانكليزية": "https://www.amsebehm2017.com/2025/06/2025_01105846167.html",
    "04_الرياضيات": "https://www.amsebehm2017.com/2025/06/2025_01974056077.html",
    "05_الاحياء": "https://www.amsebehm2017.com/2025/06/2025_29.html",
    "06_الكيمياء": "https://www.amsebehm2017.com/2025/06/2025_87.html",
    "07_الفيزياء": "https://www.amsebehm2017.com/2025/07/2025_02002914594.html",
    "08_الفرنسي": "https://www.amsebehm2017.com/2025/07/2025_5.html",
}

total = 0
for subject, url in all_subjects.items():
    print(f"\n{'='*50}")
    sname = subject[3:]
    print(f"تحميل {sname}...")
    print(f"{'='*50}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        imgs = soup.find_all("img")
        
        sub_dir = os.path.join(base_dir, subject)
        os.makedirs(sub_dir, exist_ok=True)
        
        count = 0
        for img in imgs:
            src = img.get("src", "")
            if "blogger" in src.lower():
                try:
                    img_resp = requests.get(src, headers=headers, timeout=30)
                    ext = "jpg"
                    if "png" in src:
                        ext = "png"
                    elif "webp" in src:
                        ext = "webp"
                    fname = f"{count+1}.{ext}"
                    fpath = os.path.join(sub_dir, fname)
                    with open(fpath, "wb") as f:
                        f.write(img_resp.content)
                    sz = len(img_resp.content)
                    print(f"  ✓ {fname} ({sz//1024}KB)")
                    count += 1
                    total += 1
                except Exception as e:
                    print(f"  ✗ {str(e)[:50]}")
        
        print(f"  ← {count} صور تم تحميلها لـ {sname}")
    except Exception as e:
        print(f"  ✗ خطأ في الصفحة: {str(e)[:80]}")

print(f"\n\n{'='*50}")
print(f"🎯 تم الانتهاء! {total} صورة تم تحميلها")
print(f"📁 المسار: {base_dir}")
