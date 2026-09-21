"""投喂：把新截图自动转成文字、追加到语料库，然后重新生成人格。

把"截图 -> 传电脑 -> 跑 OCR -> 生成人格"这四步合成一步。
已处理过的图片会记在 work/feed.json 里，重复投喂只会处理新图，语料是累加的。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


def default_state_path(root):
    return Path(root) / "work" / "feed.json"


def load_state(path):
    path = Path(path)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("processed", [])
                return data
        except ValueError:
            pass
    return {"images": "", "target": "", "log": "work/聊天记录.txt", "processed": []}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def image_key(path):
    """用"文件名+大小"标识一张图，避免同一张图被反复识别。"""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return "%s|%d" % (path.name, size)


def list_images(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        (item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_EXT),
        key=lambda item: item.name,
    )


def scan_folders(root):
    """平时可能放截图的文件夹：用过的、以及常见位置。"""
    folders = []
    home = Path.home()
    for item in (
        home / "Desktop",
        home / "Downloads",
        home / "Pictures",
        home / "Pictures" / "Screenshots",
        home / "Documents",
        Path("E:/aitalk"),
        Path("D:/aitalk"),
    ):
        if item.is_dir():
            folders.append(item)
    # 常见位置下面再找一层（很多人会建个"微信截图"之类的子文件夹）
    extra = []
    for folder in list(folders):
        try:
            for child in folder.iterdir():
                if child.is_dir() and any(key in child.name for key in ("截图", "微信", "chat", "Chat", "aitalk")):
                    extra.append(child)
        except OSError:
            continue
    for item in extra:
        if item not in folders:
            folders.append(item)
    return folders


def find_new_images_elsewhere(root, state, known_current):
    """当前文件夹没有新图时，去别的地方找找看。

    很多人昨天把截图放 A 文件夹、今天放 B 文件夹，固定认一个文件夹就会漏掉。
    这里只在"当前文件夹确实没有新图"时才启用，找到就自动切过去。
    """
    processed = set(state.get("processed") or [])
    candidates = []
    for folder in scan_folders(root):
        if str(folder) == str(known_current):
            continue
        try:
            images = [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_EXT]
        except OSError:
            continue
        fresh = [item for item in images if image_key(item) not in processed]
        # 只认"像一批截图"的文件夹：至少 3 张新图，或者文件夹名字就写着截图/微信。
        # 免得把下载目录里随手存的图片当成聊天记录喂进去。
        looks_like_screenshots = any(
            key in folder.name for key in ("截图", "微信", "chat", "Chat", "aitalk", "Screenshots")
        )
        if fresh and (len(fresh) >= 3 or looks_like_screenshots):
            newest = max(item.stat().st_mtime for item in fresh)
            candidates.append((len(fresh), newest, folder, fresh))
    if not candidates:
        return None
    # 优先"新图最多"的，其次"最新修改的"
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2], candidates[0][3]


def run_ocr(root, image_paths, out_file, on_progress=None):
    """调用项目里的 OCR 脚本，把图片转成"昵称: 内容"的文本。"""
    script = Path(root) / "tools" / "ocr_chat_images.ps1"
    if not script.exists():
        return False, "找不到 OCR 脚本：%s" % script
    # 路径先写进清单文件，再让脚本按清单读，避免命令行传数组时的引号/编码问题
    list_file = Path(root) / "work" / "_feed_list.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text(
        "\n".join(str(path) for path in image_paths), encoding="utf-8"
    )
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-OutFile",
        str(out_file),
        "-PhoneLayout",
        "-ListFile",
        str(list_file),
    ]
    try:
        # 实时转发 OCR 的输出，让用户能看到"第几张"的进度，而不是干等
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        lines = []
        for line in process.stdout or []:
            text = line.rstrip()
            if not text:
                continue
            lines.append(text)
            if on_progress and (text.startswith("  ") or "失败" in text or "跳过" in text):
                on_progress(text.strip())
        code = process.wait(timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "调用 OCR 失败：%s" % exc
    if code != 0:
        detail = lines[-1] if lines else "未知错误"
        return False, "OCR 没跑成功：%s" % detail
    return True, "\n".join(lines)


def append_lines(log_path, lines):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for line in lines:
            text = line.strip()
            if text:
                handle.write(text + "\n")


def count_lines(path):
    path = Path(path)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def feed(cfg, root, images=None, target=None, log=None, reset=False, state_path=None):
    """执行一次投喂，返回结果摘要 dict。"""
    root = Path(root)
    state_file = Path(state_path) if state_path else default_state_path(root)
    state = load_state(state_file)
    if reset:
        state["processed"] = []

    images_dir = images or state.get("images") or ""
    target = target or state.get("target") or ""
    log_rel = log or state.get("log") or "work/聊天记录.txt"
    log_path = Path(log_rel)
    if not log_path.is_absolute():
        log_path = root / log_rel
    if reset and log_path.exists():
        try:
            log_path.unlink()
        except OSError:
            pass

    summary = {
        "images_dir": str(images_dir),
        "target": target,
        "log": str(log_path),
        "new_images": 0,
        "recognized": 0,
        "total": 0,
        "skipped": 0,
        "ok": False,
        "message": "",
    }

    if not images_dir:
        summary["message"] = "还没有设置截图文件夹（把截图文件夹拖到 投喂.cmd 上即可）"
        return summary
    if not Path(images_dir).is_dir():
        summary["message"] = "找不到这个文件夹：%s（是不是被移动或改名了？）" % images_dir
        return summary

    all_images = list_images(images_dir)
    processed = set(state.get("processed") or [])
    todo = [item for item in all_images if image_key(item) not in processed]

    switched_from = ""
    if not todo:
        # 只在当前文件夹没有新图时"提示"一下别处有图，绝不自动切过去处理。
        # （之前试过自动切，结果把 Pictures\Screenshots 里跟聊天无关的截图
        #   也当成语料喂了进去，污染数据集。）
        found = find_new_images_elsewhere(root, state, images_dir)
        if found:
            summary["elsewhere"] = "%s（%d 张）" % (found[0], len(found[1]))

    summary["skipped"] = len(all_images) - len(todo)
    summary["new_images"] = len(todo)
    if not todo:
        summary["total"] = count_lines(log_path)
        summary["ok"] = True
        summary["message"] = (
            "没找到新截图（%s 里 %d 张都处理过了），语料仍是 %d 条。\n"
            "如果刚截了新图：确认它们放进了这个文件夹，或者把新图所在的文件夹直接拖到「投喂」图标上。"
            % (images_dir, len(all_images), summary["total"])
        )
        if summary.get("elsewhere"):
            summary["message"] += "\n（另外我在 %s 看到新图片，如果那些就是要投喂的截图，把那个文件夹拖到「投喂」上。）" % summary["elsewhere"]
        state["images"] = str(images_dir)
        if target:
            state["target"] = target
        state["log"] = log_rel
        state["folders"] = sorted(set((state.get("folders") or []) + [str(images_dir)]))
        save_state(state_file, state)
        return summary

    temp_out = root / "work" / "_feed_new.txt"
    total = len(todo)
    counter = {"done": 0}

    def show_progress(text):
        # OCR 脚本每处理完一张图会打印一行"  文件名"
        stripped = text.strip()
        if stripped.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")):
            counter["done"] += 1
            print("  [%d/%d] 已完成：%s" % (counter["done"], total, stripped))
        else:
            print("  " + stripped)

    ok, detail = run_ocr(root, todo, temp_out, on_progress=show_progress)
    if not ok:
        summary["message"] = detail
        return summary

    lines = []
    if temp_out.exists():
        lines = temp_out.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    lines = [line for line in lines if line.strip()]
    append_lines(log_path, lines)

    summary["recognized"] = len(lines)
    summary["total"] = count_lines(log_path)
    summary["ok"] = True

    for item in todo:
        processed.add(image_key(item))
    state["processed"] = sorted(processed)
    state["images"] = str(images_dir)
    state["log"] = log_rel
    state["folders"] = sorted(
        set((state.get("folders") or []) + [str(images_dir)] + ([switched_from] if switched_from else []))
    )
    if target:
        state["target"] = target
    save_state(state_file, state)
    summary["message"] = "识别了 %d 张新截图，新增 %d 条消息，累计 %d 条" % (
        len(todo),
        len(lines),
        summary["total"],
    )
    return summary
