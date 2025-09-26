
from __future__ import annotations
import os
os.environ.pop("SSLKEYLOGFILE", None)  # 取消 SSL key log
import sys
import re
import shutil
import threading
import queue
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 第三方库
import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.datadict import keyword_for_tag, tag_for_keyword
import SimpleITK as sitk

# ---------------------------- 工具函数 ---------------------------- #

def is_dicom_file(fp: Path) -> bool:
    """快速判断是否为 DICOM 文件：优先用扩展名；必要时检测 DICM 标记。"""
    try:
        if fp.suffix.lower() in {".dcm", ".dicom"}:
            return True
        # 若无典型后缀，尝试读取前 132 字节检查 'DICM'
        with open(fp, 'rb') as f:
            head = f.read(132)
            return len(head) >= 132 and head[128:132] == b'DICM'
    except Exception:
        return False


def safe_slug(s: str, for_folder: bool = False) -> str:
    """清洗字符串用于文件/文件夹名：移除非法字符，收缩空白。"""
    if s is None:
        s = ""
    s = str(s)
    # 将多余空白压缩为单个空格
    s = re.sub(r"\s+", " ", s.strip())
    # 替换非法文件名字符
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    # Windows 末尾点/空格不合法
    s = s.rstrip(" .")
    if not s:
        s = "NULL"
    return s


def normalize_key(user_key: str) -> str:
    """将用户输入的关键字宽松映射到 DICOM keyword（如 PatientName）。
    规则：去掉非字母数字，首字母大写式合并；常见别名字典兜底。
    """
    if not user_key:
        return ""
    raw = user_key.strip().lower()
    # 常见别名预映射（可按需扩展）
    alias = {
        "patient's name": "PatientName",
        "patients name": "PatientName",
        "patient name": "PatientName",
        "patientid": "PatientID",
        "patient id": "PatientID",
        "patient's birth date": "PatientBirthDate",
        "patients birth date": "PatientBirthDate",
        "birth date": "PatientBirthDate",
        "study date": "StudyDate",
        "series date": "SeriesDate",
        "acquisition date": "AcquisitionDate",
        "acquisition time": "AcquisitionTime",
        "modality": "Modality",
        "series description": "SeriesDescription",
        "study description": "StudyDescription",
        "manufacturer": "Manufacturer",
        "station name": "StationName",
    }
    if raw in alias:
        return alias[raw]

    # 一般归一：去掉非字母数字，按单词首字母大写拼接
    letters = re.sub(r"[^a-z0-9]", " ", raw).split()
    if not letters:
        return ""
    camel = "".join(w.capitalize() for w in letters)
    # 某些 DICOM keyword 带 PatientBirthDate 这种形式
    # 若末尾是 Date/Time/ID 等关键后缀，已与 DICOM keyword 接近
    return camel


def get_dicom_keyword(user_key: str) -> Optional[str]:
    """将用户输入映射为可用的 DICOM keyword（存在于 datadict 中）。
    若映射不到，返回用户归一化后的 camel 名称（pydicom 支持直接 ds.get(keyword)）。
    """
    key = normalize_key(user_key)
    if not key:
        return None
    # 若能反查 tag 则说明是合法关键字
    try:
        tg = tag_for_keyword(key)
        if tg is not None:
            return key
    except Exception:
        pass
    return key  # 允许运行时用 ds.get(key) 拉值；若无则 None


def format_value(keyword: str, value) -> str:
    """将 DICOM 元数据值转为适合文件/文件夹名的字符串。"""
    if value is None:
        return "NULL"
    try:
        # 人名：转为 Title Case，空格分隔
        if keyword in {"PatientName"}:
            text = str(value)
            # pydicom 人名可能为 'ZHANG^SAN'，替换 ^ 为 空格
            text = text.replace("^", " ")
            text = text.title()
            return safe_slug(text, for_folder=True)
        # 日期：YYYYMMDD
        if keyword.endswith("Date"):
            text = str(value).strip()
            m = re.match(r"(\d{8})", text)
            return m.group(1) if m else (text if text else "00000000")
        # 时间：HHMMSS(.ffffff) 取前 6 位
        if keyword.endswith("Time"):
            text = re.sub(r"[^0-9]", "", str(value))
            return text[:6] if text else "000000"
        # 其它：一般清洗
        return safe_slug(str(value))
    except Exception:
        return safe_slug(str(value))


def extract_keywords_values(ds: pydicom.dataset.Dataset, keywords: List[str]) -> List[str]:
    vals = []
    for k in keywords:
        v = ds.get(k, None)
        vals.append(format_value(k, v))
    return vals


@dataclass
class SeriesInfo:
    series_dir: Path                 # 直接包含 dicom 的目录
    sample_file: Path                # 用于读取元数据的样本 dicom
    rel_parts: List[str]             # 相对 root 的路径片段（尽量 case/scan/series）
    case_name: str                   # 初始 case 文件夹名（rel_parts[0]）
    scan_name: str                   # rel_parts[1] if exists else "scan"
    series_name: str                 # rel_parts[-1]
    metadata_cache: Dict[str, str] = field(default_factory=dict)  # keyword->value


class DicomScanner:
    def __init__(self, root: Path):
        self.root = root
        self.series_list: List[SeriesInfo] = []
        self.other_images: List[Path] = []  # 非 DICOM 医学影像（.mha/.nrrd/.nii 等）

    def scan(self, log: List[str]) -> None:
        self.series_list.clear()
        self.other_images.clear()
        n_dcm_series = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            p = Path(dirpath)
            # 判断该目录是否直接包含 DICOM
            dicom_files = [Path(dirpath) / f for f in filenames if is_dicom_file(Path(dirpath)/f)]
            if dicom_files:
                sample = dicom_files[0]
                rel = Path(os.path.relpath(p, self.root))
                rel_parts = rel.parts
                # 粗略推断 case/scan/series
                case_name = rel_parts[0] if len(rel_parts) >= 1 else p.name
                scan_name = rel_parts[1] if len(rel_parts) >= 2 else "scan"
                series_name = rel_parts[-1]
                self.series_list.append(SeriesInfo(
                    series_dir=p,
                    sample_file=sample,
                    rel_parts=list(rel_parts),
                    case_name=case_name,
                    scan_name=scan_name,
                    series_name=series_name,
                ))
                n_dcm_series += 1
            else:
                # 记录其它影像格式
                for f in filenames:
                    ext = Path(f).suffix.lower()
                    if f.lower().endswith('.nii.gz'):
                        continue
                    if ext in {'.nii', '.mha', '.mhd', '.nrrd', '.nifti'}:
                        self.other_images.append(Path(dirpath)/f)
        # 记录结构提示
        log.append(f"[INFO] 发现 DICOM 序列目录 {n_dcm_series} 个；其它影像文件 {len(self.other_images)} 个。")
        # 结构检查
        warn_cnt = 0
        for s in self.series_list:
            if len(s.rel_parts) < 3:
                warn_cnt += 1
        if warn_cnt:
            log.append(f"[WARN] 有 {warn_cnt} 个序列路径深度 < 3，可能不符合 root/case/scan/series 结构。将尽力推断。")

    def get_series_count(self) -> int:
        return len(self.series_list)


# ---------------------------- GUI 主类 ---------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("课题组影像处理工具 —— DICOM转NII")
        self.geometry("1200x750")
        self.minsize(1000, 650)

        self.root_dir: Optional[Path] = None
        self.scanner: Optional[DicomScanner] = None

        # 状态
        self.case_rename_mode = tk.StringVar(value="no")  # "no" or "meta"
        self.copy_mode = tk.StringVar(value="copy")       # "copy" or "inplace"
        self.convert_others = tk.BooleanVar(value=True)    # 是否同时转其它影像到 .nii.gz

        # 动态关键字输入（case 重命名 & NIfTI 命名）
        self.case_key_entries: List[tk.Entry] = []
        self.nii_key_entries: List[tk.Entry] = []

        self.dst_dir: Optional[Path] = None

        self._build_ui()

    # ---------- UI 构建 ---------- #
    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # 顶部：选择 root & 扫描
        top = ttk.Labelframe(paned, text="步骤 1：选择 root 并扫描 DICOM/结构")
        paned.add(top, weight=1)

        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(row1, text="root 目录：").pack(side=tk.LEFT)
        self.var_root = tk.StringVar()
        e_root = ttk.Entry(row1, textvariable=self.var_root)
        e_root.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(row1, text="浏览…", command=self.choose_root).pack(side=tk.LEFT)
        ttk.Button(row1, text="扫描文件结构以及 DICOM", command=self.scan_root).pack(side=tk.LEFT, padx=6)

        self.txt_log = tk.Text(top, height=8)
        self.txt_log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0,8))

        # 中部：Case 重命名
        mid = ttk.Labelframe(paned, text="步骤 2：Case（患者文件夹）重命名")
        paned.add(mid, weight=1)

        row2a = ttk.Frame(mid)
        row2a.pack(fill=tk.X, padx=8, pady=4)
        ttk.Radiobutton(row2a, text="不重命名", value="no", variable=self.case_rename_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(row2a, text="依据 metaData 重命名患者文件夹", value="meta", variable=self.case_rename_mode).pack(side=tk.LEFT, padx=(12,0))

        row2b = ttk.Frame(mid)
        row2b.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row2b, text="重命名关键字（依次拼接，缺失以 NULL 代替）：").pack(side=tk.LEFT)
        self.case_keys_frame = ttk.Frame(mid)
        self.case_keys_frame.pack(fill=tk.X, padx=8)
        btns = ttk.Frame(mid)
        btns.pack(fill=tk.X, padx=8, pady=4)
        ttk.Button(btns, text="+ 添加关键字", command=lambda: self.add_key_entry(self.case_keys_frame, self.case_key_entries)).pack(side=tk.LEFT)
        ttk.Button(btns, text="- 删除末尾", command=lambda: self.remove_key_entry(self.case_keys_frame, self.case_key_entries)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="预览重命名", command=self.preview_case_rename).pack(side=tk.LEFT, padx=12)
        ttk.Button(btns, text="确认应用", command=self.apply_case_rename).pack(side=tk.LEFT)

        # 底部：转换
        bot = ttk.Labelframe(paned, text="步骤 3：影像转 NIfTI（.nii.gz）")
        paned.add(bot, weight=2)

        row3a = ttk.Frame(bot)
        row3a.pack(fill=tk.X, padx=8, pady=4)
        ttk.Radiobutton(row3a, text="完整结构拷贝至 dst（镜像 root/case/scan/series）", value="copy", variable=self.copy_mode, command=self._toggle_dst).pack(side=tk.LEFT)
        ttk.Radiobutton(row3a, text="直接在 series 目录下生成", value="inplace", variable=self.copy_mode, command=self._toggle_dst).pack(side=tk.LEFT, padx=12)
        ttk.Button(row3a, text="选择 dst…", command=self.choose_dst).pack(side=tk.LEFT, padx=6)
        self.var_dst = tk.StringVar()
        self.ent_dst = ttk.Entry(row3a, textvariable=self.var_dst, width=50)
        self.ent_dst.pack(side=tk.LEFT, padx=(6,0))

        row3b = ttk.Frame(bot)
        row3b.pack(fill=tk.X, padx=8, pady=4)
        ttk.Checkbutton(row3b, text="同时将其它影像（.mha/.nrrd/.nii 等）统一转为 .nii.gz", variable=self.convert_others).pack(side=tk.LEFT)

        row3c = ttk.Frame(bot)
        row3c.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row3c, text="NIfTI 文件命名关键字（依次拼接，缺失以 NULL 代替）：").pack(side=tk.LEFT)
        self.nii_keys_frame = ttk.Frame(bot)
        self.nii_keys_frame.pack(fill=tk.X, padx=8)
        row3d = ttk.Frame(bot)
        row3d.pack(fill=tk.X, padx=8, pady=4)
        ttk.Button(row3d, text="+ 添加关键字", command=lambda: self.add_key_entry(self.nii_keys_frame, self.nii_key_entries)).pack(side=tk.LEFT)
        ttk.Button(row3d, text="- 删除末尾", command=lambda: self.remove_key_entry(self.nii_keys_frame, self.nii_key_entries)).pack(side=tk.LEFT, padx=6)
        ttk.Button(row3d, text="预览待生成 NIfTI", command=self.preview_nii_outputs).pack(side=tk.LEFT, padx=12)
        ttk.Button(row3d, text="确认生成", command=self.run_convert).pack(side=tk.LEFT)

        # 日志与进度
        row3e = ttk.Frame(bot)
        row3e.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4,8))
        self.pb = ttk.Progressbar(row3e, mode="determinate")
        self.pb.pack(fill=tk.X, pady=4)
        self.txt_out = tk.Text(row3e)
        self.txt_out.pack(fill=tk.BOTH, expand=True)

        # 初始化默认关键字
        for default in ["Patient's Name", "Patient's Birth Date"]:
            self.add_key_entry(self.case_keys_frame, self.case_key_entries, default)
        for default in ["Modality", "Series Description"]:
            self.add_key_entry(self.nii_keys_frame, self.nii_key_entries, default)
        self._toggle_dst()

    # ---------- 关键字输入管理 ---------- #
    def add_key_entry(self, host: ttk.Frame, store: List[tk.Entry], preset: str = ""):
        row = ttk.Frame(host)
        row.pack(fill=tk.X, pady=2)
        e = ttk.Entry(row)
        e.insert(0, preset)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        store.append(e)

    def remove_key_entry(self, host: ttk.Frame, store: List[tk.Entry]):
        if not store:
            return
        e = store.pop()
        parent = e.master
        e.destroy()
        parent.destroy()

    # ---------- 目录选择/扫描 ---------- #
    def choose_root(self):
        d = filedialog.askdirectory(title="选择 root 目录")
        if d:
            self.root_dir = Path(d)
            self.var_root.set(str(self.root_dir))

    def scan_root(self):
        if not self.root_dir or not self.root_dir.exists():
            messagebox.showwarning("提示", "请先选择有效的 root 目录！")
            return
        self._log(self.txt_log, f"[INFO] 开始扫描：{self.root_dir}")
        self.scanner = DicomScanner(self.root_dir)
        msgs: List[str] = []
        self.scanner.scan(msgs)
        for m in msgs:
            self._log(self.txt_log, m)
        self._log(self.txt_log, f"[INFO] 序列样本：")
        for i, s in enumerate(self.scanner.series_list[:10]):
            self._log(self.txt_log, f"  {i+1}. {s.series_dir}")
        if len(self.scanner.series_list) > 10:
            self._log(self.txt_log, f"  …… 共 {len(self.scanner.series_list)} 个序列目录。")

    # ---------- Case 重命名 ---------- #
    def preview_case_rename(self):
        if not self.scanner or not self.scanner.series_list:
            messagebox.showwarning("提示", "请先扫描 root。")
            return
        if self.case_rename_mode.get() != "meta":
            messagebox.showinfo("提示", "当前选择为'不重命名'。")
            return
        keys = self._collect_keys(self.case_key_entries)
        if not keys:
            messagebox.showwarning("提示", "请至少添加一个重命名关键字！")
            return
        # 以 case 为单位做聚合（同一 case 取其首个序列的样本 dicom 读取元数据）
        case_first_series: Dict[str, SeriesInfo] = {}
        for s in self.scanner.series_list:
            case_first_series.setdefault(s.case_name, s)
        mapping = []  # (old_case, new_case)
        for case, s in case_first_series.items():
            ds = self._read_dicom_header(s.sample_file)
            values = extract_keywords_values(ds, keys) if ds else ["NULL"]*len(keys)
            new_case = safe_slug("_".join(values), for_folder=True)
            mapping.append((case, new_case))
        self._show_mapping_preview("Case 重命名预览", mapping, ("原文件夹名", "新文件夹名"))

    def apply_case_rename(self):
        if not self.scanner or not self.scanner.series_list:
            messagebox.showwarning("提示", "请先扫描 root。")
            return
        if self.case_rename_mode.get() != "meta":
            messagebox.showinfo("提示", "当前选择为'不重命名'，无需应用。")
            return
        keys = self._collect_keys(self.case_key_entries)
        if not keys:
            messagebox.showwarning("提示", "请至少添加一个重命名关键字！")
            return
        # 收集 case 重命名映射
        case_first_series: Dict[str, SeriesInfo] = {}
        for s in self.scanner.series_list:
            case_first_series.setdefault(s.case_name, s)
        # 构建 new name，先做预检查避免冲突
        rename_plan: List[Tuple[Path, Path]] = []
        used_new: set[str] = set()
        for case, s in case_first_series.items():
            ds = self._read_dicom_header(s.sample_file)
            values = extract_keywords_values(ds, keys) if ds else ["NULL"]*len(keys)
            new_case = safe_slug("_".join(values), for_folder=True)
            # 冲突处理
            base_new = new_case
            idx = 2
            while new_case in used_new or (self.root_dir / new_case).exists():
                new_case = f"{base_new}_{idx}"
                idx += 1
            used_new.add(new_case)
            old_path = self.root_dir / case
            new_path = self.root_dir / new_case
            if old_path != new_path:
                rename_plan.append((old_path, new_path))
        if not rename_plan:
            messagebox.showinfo("提示", "没有需要重命名的文件夹（可能已是目标命名）。")
            return
        # 确认
        if not messagebox.askyesno("确认", f"将重命名 {len(rename_plan)} 个 case 文件夹，是否继续？"):
            return
        # 执行
        ok, fail = 0, 0
        for old, new in rename_plan:
            try:
                old.rename(new)
                ok += 1
            except Exception as e:
                self._log(self.txt_out, f"[ERR] 重命名失败：{old} -> {new} ：{e}")
                fail += 1
        self._log(self.txt_out, f"[DONE] Case 重命名完成：成功 {ok}，失败 {fail}")
        # 重新扫描以更新路径（重要）
        self.scan_root()

    # ---------- NIfTI 预览/生成 ---------- #
    def _collect_keys(self, entries: List[tk.Entry]) -> List[str]:
        raw = [e.get().strip() for e in entries if e.get().strip()]
        kws = []
        for r in raw:
            kw = get_dicom_keyword(r)
            if kw:
                kws.append(kw)
        return kws

    def _read_dicom_header(self, fp: Path) -> Optional[pydicom.dataset.Dataset]:
        try:
            return pydicom.dcmread(str(fp), stop_before_pixels=True, force=True)
        except InvalidDicomError:
            return None
        except Exception:
            return None

    def _series_output_path(self, s: SeriesInfo, filename: str) -> Path:
        if self.copy_mode.get() == "copy":
            if not self.dst_dir:
                raise RuntimeError("未选择 dst 目录！")
            # 若 case 重命名已应用，series_list 中的 case_name 已更新（因重扫）
            out_dir = self.dst_dir / s.case_name / s.scan_name / s.series_name
            out_dir.mkdir(parents=True, exist_ok=True)
            return out_dir / filename
        else:
            return s.series_dir / filename

    def preview_nii_outputs(self):
        if not self.scanner or not self.scanner.series_list:
            messagebox.showwarning("提示", "请先扫描 root。")
            return
        keys = self._collect_keys(self.nii_key_entries)
        if not keys:
            messagebox.showwarning("提示", "请至少添加一个 NIfTI 命名关键字！")
            return
        preview_rows = []  # [(series_dir, out_path)]
        for s in self.scanner.series_list:
            ds = self._read_dicom_header(s.sample_file)
            values = extract_keywords_values(ds, keys) if ds else ["NULL"]*len(keys)
            basename = safe_slug("_".join(values)) + ".nii.gz"
            try:
                outp = self._series_output_path(s, basename)
            except Exception as e:
                outp = Path("<错误：未选择 dst>")
            preview_rows.append((str(s.series_dir), str(outp)))
        self._show_mapping_preview("NIfTI 生成预览", preview_rows, ("Series 目录", "输出 NIfTI 路径"))

    def run_convert(self):
        if not self.scanner or not self.scanner.series_list:
            messagebox.showwarning("提示", "请先扫描 root。")
            return
        if self.copy_mode.get() == "copy" and (not self.dst_dir or not self.dst_dir.exists()):
            messagebox.showwarning("提示", "请选择有效的 dst 目录！")
            return
        keys = self._collect_keys(self.nii_key_entries)
        if not keys:
            messagebox.showwarning("提示", "请至少添加一个 NIfTI 命名关键字！")
            return
        # 启动线程执行，避免阻塞 UI
        t = threading.Thread(target=self._convert_worker, args=(keys,))
        t.daemon = True
        t.start()

    def _convert_worker(self, keys: List[str]):
        series = self.scanner.series_list
        total_tasks = len(series)
        other_files = self.scanner.other_images if self.convert_others.get() else []
        total_tasks += len(other_files)
        self._set_progress(0, total_tasks)
        done = 0

        # DICOM → NIfTI
        for s in series:
            try:
                ds = self._read_dicom_header(s.sample_file)
                values = extract_keywords_values(ds, keys) if ds else ["NULL"]*len(keys)
                basename = safe_slug("_".join(values)) + ".nii.gz"
                outp = self._series_output_path(s, basename)
                outp = self._avoid_conflict(outp)
                # 读 DICOM 序列并写 NIfTI
                self._dicom_series_to_nifti(s.series_dir, outp)
                self._log(self.txt_out, f"[OK] {s.series_dir} -> {outp}")
            except Exception as e:
                self._log(self.txt_out, f"[ERR] 转换失败：{s.series_dir} ：{e}")
            done += 1
            self._set_progress(done, total_tasks)

        # 其它影像统一转 .nii.gz
        for src in other_files:
            try:
                if self.copy_mode.get() == "copy":
                    # 镜像结构：将 src 相对 root 的层级按原样放到 dst 下
                    rel = Path(os.path.relpath(src.parent, self.root_dir))
                    out_dir = (self.dst_dir / rel)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_file = out_dir / (src.stem + ".nii.gz")
                else:
                    out_file = src.with_suffix("")  # 去除一层后缀
                    # 针对 .nii.gz 情况，stem 不准，统一如下：
                    if str(src).lower().endswith('.nii'):
                        out_file = src.with_suffix("")
                    elif str(src).lower().endswith('.mha'):
                        out_file = src.with_suffix("")
                    elif str(src).lower().endswith('.mhd'):
                        out_file = src.with_suffix("")
                    elif str(src).lower().endswith('.nrrd'):
                        out_file = src.with_suffix("")
                    out_file = out_file.with_suffix(".nii.gz")
                out_file = self._avoid_conflict(out_file)
                self._generic_to_nifti(src, out_file)
                self._log(self.txt_out, f"[OK] 其它影像：{src} -> {out_file}")
            except Exception as e:
                self._log(self.txt_out, f"[ERR] 其它影像转换失败：{src} ：{e}")
            done += 1
            self._set_progress(done, total_tasks)

        self._log(self.txt_out, "[DONE] 全部转换完成。")

    # ---------- 实际转换实现 ---------- #
    def _dicom_series_to_nifti(self, series_dir: Path, out_path: Path):
        # 优先用 SimpleITK 的系列读取接口自动排序
        reader = sitk.ImageSeriesReader()
        series_uids = reader.GetGDCMSeriesIDs(str(series_dir))
        file_names: List[str]
        if series_uids:
            # 通常是 1 个 UID；若多个，取第 1 个
            uid = series_uids[0]
            file_names = reader.GetGDCMSeriesFileNames(str(series_dir), uid)
            reader.SetFileNames(file_names)
            img = reader.Execute()
        else:
            # 兜底：手动收集目录下的 DICOM 文件
            dicoms = [str(series_dir / f) for f in os.listdir(series_dir) if is_dicom_file(series_dir / f)]
            if not dicoms:
                raise RuntimeError("未在目录中找到 DICOM 文件")
            reader.SetFileNames(sorted(dicoms))
            img = reader.Execute()
        # 写 NIfTI（.nii.gz）
        sitk.WriteImage(img, str(out_path))

    def _generic_to_nifti(self, src_path: Path, out_path: Path):
        img = sitk.ReadImage(str(src_path))
        sitk.WriteImage(img, str(out_path))

    def _avoid_conflict(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = "".join(path.suffixes)
        parent = path.parent
        i = 2
        while True:
            cand = parent / f"{stem}_{i}{suffix}"
            if not cand.exists():
                return cand
            i += 1

    # ---------- 预览窗口/日志/进度 ---------- #
    def _show_mapping_preview(self, title: str, rows: List[Tuple[str, str]], headers: Tuple[str, str]):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("900x500")
        tree = ttk.Treeview(win, columns=("c1", "c2"), show="headings")
        tree.heading("c1", text=headers[0])
        tree.heading("c2", text=headers[1])
        tree.pack(fill=tk.BOTH, expand=True)
        for a, b in rows:
            tree.insert('', tk.END, values=(a, b))
        ttk.Label(win, text=f"共 {len(rows)} 条").pack(pady=4)

    def _toggle_dst(self):
        if self.copy_mode.get() == "copy":
            self.ent_dst.configure(state=tk.NORMAL)
        else:
            self.ent_dst.configure(state=tk.DISABLED)

    def choose_dst(self):
        d = filedialog.askdirectory(title="选择 dst 输出目录")
        if d:
            self.dst_dir = Path(d)
            self.var_dst.set(str(self.dst_dir))

    def _log(self, widget: tk.Text, msg: str):
        widget.insert(tk.END, msg + "\n")
        widget.see(tk.END)
        widget.update_idletasks()

    def _set_progress(self, cur: int, total: int):
        self.pb.config(maximum=total)
        self.pb.config(value=cur)
        self.pb.update_idletasks()


if __name__ == '__main__':
    app = App()
    app.mainloop()
