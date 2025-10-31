# -*- coding: utf-8 -*-
import os
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from huggingface_hub import HfApi, snapshot_download, login as hf_login

# -------------------- 默认配置（可在 GUI 中修改） --------------------
DEFAULT_REPO_ID = "ibrahimhamamci/CT-RATE"
DEFAULT_REVISION = "daddf2f4ff5cfbcebc70756462bb9518d5585246"
DEFAULT_REMOTE_DIR = "dataset/train"

# -------------------- 工具函数：列出一级子目录（兼容不同 hub 版本） --------------------
def list_first_level_dirs(api: HfApi, repo_id: str, revision: str, base: str):
    """
    返回 base/ 下的一级子目录绝对路径列表（形如 dataset/train/train_1）
    先尝试 list_repo_tree(path_in_repo=...)；不支持时回退到 list_repo_files 聚合。
    """
    # 新 API：list_repo_tree + path_in_repo
    try:
        tree = api.list_repo_tree(
            repo_id=repo_id,
            repo_type="dataset",
            revision=revision,
            recursive=False,
            path_in_repo=base,
        )
        dirs = sorted(e.path for e in tree if getattr(e, "type", "") == "directory")
        if dirs:
            return dirs
    except TypeError:
        pass
    except Exception:
        pass

    # 回退：list_repo_files 全量，再聚合出第一层目录
    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset", revision=revision)
    prefix = base.rstrip("/") + "/"
    subdir_set = set()
    for f in files:
        if f.startswith(prefix):
            rest = f[len(prefix):]
            if "/" in rest:
                subdir = rest.split("/", 1)[0]
                subdir_set.add(f"{base}/{subdir}")
    return sorted(subdir_set)

# -------------------- GUI 应用 --------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CT-RATE 子目录分段下载器")
        self.geometry("880x600")

        self._build_ui()

        # 内部状态
        self.subdirs = []  # 列出的子目录
        self.downloading = False

    # UI 结构
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        frm_top = ttk.LabelFrame(self, text="基本配置")
        frm_top.pack(fill="x", **pad)

        ttk.Label(frm_top, text="REPO_ID:").grid(row=0, column=0, sticky="e")
        self.repo_entry = ttk.Entry(frm_top, width=50)
        self.repo_entry.insert(0, DEFAULT_REPO_ID)
        self.repo_entry.grid(row=0, column=1, sticky="we", columnspan=3, **pad)

        ttk.Label(frm_top, text="REVISION:").grid(row=1, column=0, sticky="e")
        self.rev_entry = ttk.Entry(frm_top, width=50)
        self.rev_entry.insert(0, DEFAULT_REVISION)
        self.rev_entry.grid(row=1, column=1, sticky="we", columnspan=3, **pad)

        ttk.Label(frm_top, text="REMOTE_DIR:").grid(row=2, column=0, sticky="e")
        self.remote_entry = ttk.Entry(frm_top, width=50)
        self.remote_entry.insert(0, DEFAULT_REMOTE_DIR)
        self.remote_entry.grid(row=2, column=1, sticky="we", columnspan=3, **pad)

        ttk.Label(frm_top, text="LOCAL_DIR:").grid(row=3, column=0, sticky="e")
        self.local_entry = ttk.Entry(frm_top, width=50)
        self.local_entry.insert(0, str(Path.cwd()))
        self.local_entry.grid(row=3, column=1, sticky="we", **pad)
        ttk.Button(frm_top, text="选择...", command=self.choose_local).grid(row=3, column=2, **pad)

        ttk.Label(frm_top, text="token (可留空用环境变量)：").grid(row=4, column=0, sticky="e")
        self.token_entry = ttk.Entry(frm_top, width=50, show="*")
        # 默认读环境
        default_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN") or ""
        if default_token:
            self.token_entry.insert(0, default_token)
        self.token_entry.grid(row=4, column=1, sticky="we", columnspan=3, **pad)

        self.use_mirror = tk.BooleanVar(value=True)
        self.disable_h2 = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_top, text="使用镜像（hf-mirror.com）", variable=self.use_mirror).grid(row=5, column=1, sticky="w")
        ttk.Checkbutton(frm_top, text="关闭 HTTP/2（避免 TLS/EOF 报错）", variable=self.disable_h2).grid(row=5, column=2, sticky="w")

        frm_mid = ttk.LabelFrame(self, text="下载范围（按字典序子目录下标，左闭右闭）")
        frm_mid.pack(fill="x", **pad)

        ttk.Label(frm_mid, text="strat_index:").grid(row=0, column=0, sticky="e")
        self.start_entry = ttk.Entry(frm_mid, width=10)
        self.start_entry.insert(0, "0")
        self.start_entry.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(frm_mid, text="end_index:").grid(row=0, column=2, sticky="e")
        self.end_entry = ttk.Entry(frm_mid, width=10)
        self.end_entry.insert(0, "9")
        self.end_entry.grid(row=0, column=3, sticky="w", **pad)

        self.count_var = tk.StringVar(value="子目录数量：未获取")
        ttk.Label(frm_mid, textvariable=self.count_var).grid(row=0, column=4, sticky="w")

        ttk.Button(frm_mid, text="浏览子目录", command=self.list_subdirs_action).grid(row=0, column=5, **pad)

        frm_btn = ttk.Frame(self)
        frm_btn.pack(fill="x", **pad)
        self.start_btn = ttk.Button(frm_btn, text="开始下载", command=self.start_download_thread, state="disabled")
        self.start_btn.pack(side="left")

        self.pb = ttk.Progressbar(self, mode="determinate")
        self.pb.pack(fill="x", **pad)

        frm_log = ttk.LabelFrame(self, text="日志 / 进度")
        frm_log.pack(fill="both", expand=True, **pad)

        self.log_txt = tk.Text(frm_log, height=18)
        self.log_txt.pack(fill="both", expand=True)

    # 选择本地目录
    def choose_local(self):
        d = filedialog.askdirectory(title="选择本地输出目录", initialdir=self.local_entry.get())
        if d:
            self.local_entry.delete(0, "end")
            self.local_entry.insert(0, d)

    # 输出日志
    def log(self, s: str):
        self.log_txt.insert("end", s.rstrip() + "\n")
        self.log_txt.see("end")
        self.update_idletasks()

    # 设置环境变量（镜像/HTTP2）
    def apply_env(self):
        if self.use_mirror.get():
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        else:
            os.environ.pop("HF_ENDPOINT", None)
        if self.disable_h2.get():
            os.environ["HF_HUB_DISABLE_HTTP2"] = "1"
        else:
            os.environ.pop("HF_HUB_DISABLE_HTTP2", None)

    # 浏览子目录
    def list_subdirs_action(self):
        try:
            self.apply_env()
            repo = self.repo_entry.get().strip()
            rev = self.rev_entry.get().strip()
            remote = self.remote_entry.get().strip()
            token = self.token_entry.get().strip() or None
            if token:
                try:
                    hf_login(token=token)  # 写入本机缓存（安全：仅当前用户）
                except Exception as e:
                    self.log(f"[WARN] 登录失败（但不阻塞浏览）：{e}")

            api = HfApi()
            self.log(f"[INFO] 正在列出 {repo}@{rev} -> {remote} 的子目录 ...")
            dirs = list_first_level_dirs(api, repo, rev, remote)
            self.subdirs = dirs
            self.count_var.set(f"子目录数量：{len(dirs)}")
            self.log(f"[OK] 共 {len(dirs)} 个子目录。")
            if len(dirs) > 0:
                self.start_btn.config(state="normal")
            else:
                self.start_btn.config(state="disabled")
        except Exception as e:
            self.start_btn.config(state="disabled")
            messagebox.showerror("错误", f"浏览子目录失败：\n{e}")
            self.log(f"[ERROR] 浏览子目录失败：{e}")

    # 启动下载线程
    def start_download_thread(self):
        if self.downloading:
            return
        t = threading.Thread(target=self.download_worker, daemon=True)
        t.start()

    # 下载主逻辑（子线程）
    def download_worker(self):
        try:
            self.downloading = True
            self.start_btn.config(state="disabled")
            self.apply_env()

            repo = self.repo_entry.get().strip()
            rev = self.rev_entry.get().strip()
            remote = self.remote_entry.get().strip()
            token = self.token_entry.get().strip() or True  # True：用缓存或环境变量
            local_root = Path(self.local_entry.get().strip()).expanduser()
            local_root.mkdir(parents=True, exist_ok=True)

            # 处理索引范围
            try:
                si = int(self.start_entry.get().strip())
                ei = int(self.end_entry.get().strip())
            except ValueError:
                messagebox.showerror("错误", "strat_index / end_index 必须是整数")
                self.log("[ERROR] strat_index / end_index 必须是整数")
                return

            if not self.subdirs:
                messagebox.showwarning("提示", "请先点击『浏览子目录』获取列表。")
                self.log("[WARN] 未获取到子目录，请先『浏览子目录』。")
                return

            if si < 0 or ei < si or si >= len(self.subdirs):
                messagebox.showerror("错误", f"索引范围不合法（共有 {len(self.subdirs)} 个子目录）")
                self.log("[ERROR] 索引范围不合法。")
                return

            ei = min(ei, len(self.subdirs) - 1)
            slice_dirs = self.subdirs[si:ei + 1]
            out_dir = local_root / f"CT-RATE_dwonload_{si}-{ei}"
            out_dir.mkdir(parents=True, exist_ok=True)

            total = len(slice_dirs)
            self.pb.config(maximum=total, value=0)
            self.log(f"[INFO] 计划下载 {total} 个子目录，输出到：{out_dir}")

            # 逐个目录下载（便于显示每个目录完成进度）
            ok = 0
            for idx, d in enumerate(slice_dirs, 1):
                allow_patterns = [f"{d}/**"]
                self.log(f"[{idx}/{total}] 下载：{d} ...")
                try:
                    snapshot_download(
                        repo_id=repo,
                        repo_type="dataset",
                        revision=rev,
                        local_dir=str(out_dir),
                        # 下面两个参数在新版本已忽略，但保留也无碍
                        # local_dir_use_symlinks=False,
                        # resume_download=True,
                        max_workers=2,
                        etag_timeout=30,
                        allow_patterns=allow_patterns,
                        ignore_patterns=[".git/*"],
                        token=token,
                    )
                    ok += 1
                    self.log(f"  -> 完成：{d}")
                except Exception as e:
                    self.log(f"  -> 失败：{d} | {e}")
                finally:
                    self.pb.step(1)
                    self.update_idletasks()

            self.log(f"[DONE] 完成 {ok}/{total} 个子目录。保存于：{out_dir}")
            if ok == 0:
                self.log("  * 如遇 401/403，请确认：已在网页 Access/Agree、token 具备 Read 权限、当前机器已登录。")
        finally:
            self.downloading = False
            self.start_btn.config(state="normal")

# -------------------- 入口 --------------------
if __name__ == "__main__":
    # 可在此设置一次性的“默认稳妥项”（也可在 GUI 里勾选）
    # os.environ.setdefault("HF_HUB_DISABLE_HTTP2", "1")

    app = App()
    app.mainloop()
