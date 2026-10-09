"""Apply a website update request (a GitHub issue made from one of the forms in
.github/ISSUE_TEMPLATE) to the site checked out in the current directory.

Reads the issue from $ISSUE_TITLE / $ISSUE_BODY, edits data/site.json, saves any
attached photos under images/, and writes a short report to $REPORT_FILE.
"""
import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image, ImageOps

TOKEN = os.environ.get("GITHUB_TOKEN", "")
TITLE = os.environ.get("ISSUE_TITLE", "")
BODY = os.environ.get("ISSUE_BODY", "") or ""
REPORT = os.environ.get("REPORT_FILE", "report.md")
SITE = "data/site.json"

IMG_RE = re.compile(
    r'(https://(?:github\.com/user-attachments/assets/[\w-]+'
    r'|github\.com/[\w.-]+/[\w.-]+/assets/[\w/-]+'
    r'|(?:private-)?user-images\.githubusercontent\.com/[^\s)"\'<>]+))'
)


def sections(body):
    """Issue-form bodies look like '### Label\\n\\nvalue'. Return {label_lower: value}."""
    out = {}
    for part in re.split(r"^###\s+", body, flags=re.M)[1:]:
        label, _, value = part.partition("\n")
        value = value.strip()
        if value == "_No response_":
            value = ""
        out[label.strip().lower()] = value
    return out


def field(sec, *starts):
    for k, v in sec.items():
        if any(k.startswith(s.lower()) for s in starts):
            return v
    return ""


def images_in(text):
    seen = []
    for u in IMG_RE.findall(text or ""):
        if u not in seen:
            seen.append(u)
    return seen


def download(url, prefix):
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    r = requests.get(url, headers=headers, timeout=60)  # requests drops auth on cross-host redirects
    if r.status_code in (401, 403, 404) and headers:
        r = requests.get(url, timeout=60)
    r.raise_for_status()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
    img.thumbnail((1600, 1600))
    os.makedirs("images", exist_ok=True)
    n = 0
    while True:
        name = f"images/{prefix}-{int(time.time())}-{n}.jpg"
        if not os.path.exists(name):
            break
        n += 1
    img.save(name, "JPEG", quality=85, optimize=True)
    return name


def remove_file(path):
    if path and path.startswith("images/") and os.path.exists(path):
        os.remove(path)


def find(items, key, value):
    v = value.strip().lower()
    for i, it in enumerate(items):
        if str(it.get(key, "")).strip().lower() == v:
            return i
    for i, it in enumerate(items):  # forgiving match: "Nazeer" finds "Sir Nazeer Ahmed Jamali"
        if v and v in str(it.get(key, "")).strip().lower():
            return i
    return -1


def main():
    with open(SITE, encoding="utf-8") as f:
        data = json.load(f)
    for k in ("gallery", "notices", "teachers", "courses"):
        data.setdefault(k, [])
    sec = sections(BODY)
    kind = (re.match(r"\s*\[([^\]]+)\]", TITLE) or [None, ""])[1].strip().lower()
    done = []

    if kind == "gallery":
        urls = images_in(field(sec, "photos") or BODY)
        if not urls:
            raise ValueError("No photos were attached.")
        caption = field(sec, "caption")
        new = [{"src": download(u, "gallery"), "caption": caption} for u in urls]
        data["gallery"] = new + data["gallery"]
        done.append(f"Added {len(new)} photo(s) to the Gallery.")

    elif kind == "notice":
        action = field(sec, "what do you want").lower()
        text = field(sec, "notice text")
        if "remove all" in action:
            data["notices"] = []
            done.append("Removed all notices.")
        if not action.startswith("remove all notices"):
            if not text:
                raise ValueError("Notice text is empty.")
            data["notices"].insert(0, {"date": time.strftime("%Y-%m-%d"), "text": text})
            done.append(f"Added notice: “{text}”.")

    elif kind == "teacher":
        action = field(sec, "what do you want").lower()
        name = field(sec, "teacher name")
        i = find(data["teachers"], "name", name)
        if action.startswith("remove"):
            if i < 0:
                raise ValueError(f"Teacher “{name}” not found.")
            t = data["teachers"].pop(i)
            remove_file(t.get("photo"))
            done.append(f"Removed teacher {t['name']}.")
        else:
            urls = images_in(field(sec, "photo"))
            if action.startswith("add a new"):
                if i >= 0:
                    raise ValueError(f"Teacher “{data['teachers'][i]['name']}” already exists.")
                data["teachers"].append({"name": name, "subject": field(sec, "subject"), "photo": ""})
                i = len(data["teachers"]) - 1
                done.append(f"Added teacher {name}.")
            elif i < 0:
                raise ValueError(f"Teacher “{name}” not found. Check the spelling.")
            if urls:
                old = data["teachers"][i].get("photo")
                data["teachers"][i]["photo"] = download(urls[0], "teacher")
                remove_file(old)
                done.append(f"Updated photo of {data['teachers'][i]['name']}.")
            elif not action.startswith("add a new"):
                raise ValueError("No photo was attached.")

    elif kind == "fee":
        course = field(sec, "course name")
        i = find(data["courses"], "name", course)
        if i < 0:
            raise ValueError(f"Course “{course}” not found. Check the spelling.")
        fee = re.sub(r"[^\d]", "", field(sec, "new monthly fee"))
        teacher = field(sec, "new teacher")
        c = data["courses"][i]
        if fee:
            c["fee"] = int(fee)
            done.append(f"{c['name']} fee is now Rs {int(fee):,}.")
        if teacher:
            c["teacher"] = teacher
            done.append(f"{c['name']} teacher is now {teacher}.")
        if not done:
            raise ValueError("Nothing to change: fee and teacher were both blank.")

    elif kind == "logo":
        which = field(sec, "which picture").lower()
        urls = images_in(field(sec, "photo"))
        if not urls:
            raise ValueError("No photo was attached.")
        key = "logo" if which.startswith("logo") else "heroImage"
        old = data["academy"].get(key)
        data["academy"][key] = download(urls[0], key.lower())
        remove_file(old)
        done.append("Updated the logo." if key == "logo" else "Updated the main banner photo.")

    elif kind == "remove photo":
        raw = field(sec, "photo numbers").strip()
        if raw.lower() == "all":
            for g in data["gallery"]:
                remove_file(g.get("src"))
            done.append(f"Removed all {len(data['gallery'])} gallery photos.")
            data["gallery"] = []
        else:
            nums = sorted({int(n) for n in re.findall(r"\d+", raw)}, reverse=True)
            bad = [n for n in nums if not 1 <= n <= len(data["gallery"])]
            if not nums or bad:
                raise ValueError(f"Photo number(s) {bad or raw} not found. The gallery has {len(data['gallery'])} photo(s).")
            for n in nums:
                remove_file(data["gallery"].pop(n - 1).get("src"))
            done.append(f"Removed photo(s) {', '.join(map(str, sorted(nums)))}.")

    elif kind == "contact":
        c = data["contact"]
        for label, key in [("address", "address"), ("whatsapp", "whatsapp"), ("telephone", "phone"),
                           ("facebook", "facebook"), ("map location", "mapQuery")]:
            v = field(sec, label)
            if v:
                c[key] = v
                done.append(f"Updated {label}.")
        if not done:
            raise ValueError("Nothing to change: all boxes were blank.")

    else:
        raise ValueError("This is not a website update form.")

    with open(SITE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return done


if __name__ == "__main__":
    try:
        lines = main()
        msg = "✅ **Done!** The website will show the change in about 1–2 minutes.\n\n" + "\n".join(f"- {l}" for l in lines)
        code = 0
    except Exception as e:  # report every failure back on the issue
        msg = f"❌ **Could not update the website:** {e}\n\nPlease fix it and submit the form again."
        code = 1
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)
    sys.exit(code)
