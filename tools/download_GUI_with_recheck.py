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
        self.geometry("980x640")

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

        ttk.Label(frm_mid, text="start_index:").grid(row=0, column=0, sticky="e")  # UPDATED: 统一命名
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

        self.check_btn = ttk.Button(frm_btn, text="检查未下载项目", command=self.check_missing_action, state="disabled")
        self.check_btn.pack(side="left", padx=6)

        self.fill_missing_btn = ttk.Button(  # NEW
            frm_btn, text="下载未下载项", command=self.download_missing_action, state="disabled"
        )
        self.fill_missing_btn.pack(side="left", padx=6)

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

            base_url = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
            api = HfApi(endpoint=base_url)  # 强制使用镜像或官方

            self.log(f"[INFO] 正在列出 {repo}@{rev} -> {remote} 的子目录 ...")
            dirs = list_first_level_dirs(api, repo, rev, remote)
            self.subdirs = dirs
            self.count_var.set(f"子目录数量：{len(dirs)}")
            self.log(f"[OK] 共 {len(dirs)} 个子目录。")
            if len(dirs) > 0:
                self.start_btn.config(state="normal")
                self.check_btn.config(state="normal")
                self.fill_missing_btn.config(state="normal")
            else:
                self.start_btn.config(state="disabled")
                self.check_btn.config(state="disabled")
                self.fill_missing_btn.config(state="disabled")
        except Exception as e:
            self.start_btn.config(state="disabled")
            self.check_btn.config(state="disabled")
            self.fill_missing_btn.config(state="disabled")
            messagebox.showerror("错误", f"浏览子目录失败：\n{e}")
            self.log(f"[ERROR] 浏览子目录失败：{e}")

    # 启动下载线程（全范围）
    def start_download_thread(self):
        if self.downloading:
            return
        t = threading.Thread(target=self.download_worker, daemon=True)
        t.start()

    # 下载主逻辑（子线程）——全范围
    def download_worker(self):
        try:
            self.downloading = True
            self._toggle_buttons(False)
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
                messagebox.showerror("错误", "start_index / end_index 必须是整数")
                self.log("[ERROR] start_index / end_index 必须是整数")
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
            out_dir = local_root / f"CT-RATE_download_{si}-{ei}"
            out_dir.mkdir(parents=True, exist_ok=True)

            total = len(slice_dirs)
            self.pb.config(maximum=total, value=0)
            self.log(f"[INFO] 计划下载 {total} 个子目录，输出到：{out_dir}")

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
            self._toggle_buttons(True)

    # NEW: 判断目录内是否包含至少一个实际文件（排除隐藏）
    def _dir_has_any_file(self, p: Path) -> bool:
        if not p.exists() or not p.is_dir():
            return False
        for sub in p.rglob("*"):
            if sub.is_file() and not sub.name.startswith("."):
                return True
        return False

    # NEW: 计算当前范围内缺失的 train_xx 列表（返回 repo 内路径 & 简名）
    def _collect_missing(self):
        if not self.subdirs:
            raise RuntimeError("未获取到子目录列表，请先『浏览子目录』。")

        try:
            si = int(self.start_entry.get().strip())
            ei = int(self.end_entry.get().strip())
        except ValueError:
            raise RuntimeError("start_index / end_index 必须是整数")

        if si < 0 or ei < si or si >= len(self.subdirs):
            raise RuntimeError(f"索引范围不合法（共有 {len(self.subdirs)} 个子目录）")

        ei = min(ei, len(self.subdirs) - 1)
        slice_dirs = self.subdirs[si:ei + 1]

        local_root = Path(self.local_entry.get().strip()).expanduser()
        out_dir = local_root / f"CT-RATE_download_{si}-{ei}"

        missing_repo_paths = []
        missing_names = []

        for d in slice_dirs:
            full = out_dir / d  # out_dir/dataset/train/train_xx
            if not self._dir_has_any_file(full):
                missing_repo_paths.append(d)          # 形如 dataset/train/train_xx
                missing_names.append(d.split("/")[-1])  # 形如 train_xx

        return out_dir, missing_repo_paths, missing_names

    # NEW: 检查未下载项目（仅展示）
    def check_missing_action(self):
        try:
            out_dir, missing_repo_paths, missing_names = self._collect_missing()
            total = (int(self.end_entry.get()) - int(self.start_entry.get()) + 1)
            ok_cnt = total - len(missing_repo_paths)
            self.log(f"[CHECK] 扫描本地：{out_dir}")
            self.log("  期望目录\t\t状态\t\t本地路径")
            self.log("  ----------\t\t----\t\t--------")

            # 重新打印每一项状态
            si = int(self.start_entry.get()); ei = int(self.end_entry.get())
            slice_dirs = self.subdirs[si:ei + 1]
            for d in slice_dirs:
                full = out_dir / d
                train_name = d.split("/")[-1]
                status = "MISSING" if train_name in missing_names else "OK"
                self.log(f"  {train_name:<16}\t{status:<8}\t{full}")

            self.log(f"[RESULT] OK: {ok_cnt}/{total} | MISSING: {len(missing_repo_paths)}")
            if missing_repo_paths:
                messagebox.showwarning("检查结果", f"缺失 {len(missing_repo_paths)}/{total} 个：\n" + ", ".join(missing_names))
            else:
                messagebox.showinfo("检查结果", f"全部就绪：{ok_cnt}/{total} 个子目录均存在且包含文件。")
        except Exception as e:
            messagebox.showerror("错误", f"检查未下载项目时出错：\n{e}")
            self.log(f"[ERROR] 检查未下载项目失败：{e}")

    # NEW: 仅下载缺失项
    def download_missing_action(self):
        if self.downloading:
            return
        t = threading.Thread(target=self._download_missing_worker, daemon=True)
        t.start()

    # NEW: 缺失项下载子线程
    def _download_missing_worker(self):
        try:
            self.downloading = True
            self._toggle_buttons(False)
            self.apply_env()

            repo = self.repo_entry.get().strip()
            rev = self.rev_entry.get().strip()
            token = self.token_entry.get().strip() or True
            out_dir, missing_repo_paths, missing_names = self._collect_missing()

            if not missing_repo_paths:
                self.log("[INFO] 没有缺失项需要下载。")
                messagebox.showinfo("下载未下载项", "没有发现缺失项。")
                return

            self.log(f"[INFO] 仅下载缺失的 {len(missing_repo_paths)} 个目录 -> {out_dir}")
            self.pb.config(maximum=len(missing_repo_paths), value=0)

            ok = 0
            for idx, d in enumerate(missing_repo_paths, 1):
                self.log(f"[{idx}/{len(missing_repo_paths)}] 下载缺失：{d} ...")
                try:
                    snapshot_download(
                        repo_id=repo,
                        repo_type="dataset",
                        revision=rev,
                        local_dir=str(out_dir),
                        max_workers=2,
                        etag_timeout=30,
                        allow_patterns=[f"{d}/**"],
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

            self.log(f"[DONE] 缺失项下载完成：{ok}/{len(missing_repo_paths)}")
            if ok < len(missing_repo_paths):
                self.log("  * 如遇 401/403，请确认：已在网页 Access/Agree、token 具备 Read 权限、当前机器已登录。")
        except Exception as e:
            messagebox.showerror("错误", f"下载未下载项时出错：\n{e}")
            self.log(f"[ERROR] 下载未下载项失败：{e}")
        finally:
            self.downloading = False
            self._toggle_buttons(True)

    # NEW: 统一开关按钮状态
    def _toggle_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.start_btn.config(state=state)
        self.check_btn.config(state=state)
        self.fill_missing_btn.config(state=state)

# -------------------- 入口 --------------------
if __name__ == "__main__":
    # os.environ.setdefault("HF_HUB_DISABLE_HTTP2", "1")
    app = App()
    app.mainloop()
