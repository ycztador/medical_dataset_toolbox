# medical_dataset_toolbox

> 影像数据规范化处理的一站式小工具集（含 3 个可视化 GUI）：  
> **DICOM → NIfTI 转换、批量重命名与“真正格式转换”、按 Excel 划分 Train/Test/Val**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![OS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-informational)](#)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)
<!-- 如果已确定开源协议，可替换下面的 Badge；否则先留空或写 TBD -->
<!-- [![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) -->

---

## 目录
- [项目简介](#项目简介)
- [安装与环境](#安装与环境)
- [快速上手（90 秒）](#快速上手90-秒)
- [工具总览](#工具总览)
- [使用说明](#使用说明)
  - [1) DICOM 转 NIfTI：`image_dcm2nii_new.py`](#1-dicom-转-niftiimage_dcm2nii_newpy)
  - [2) 重命名与“真正格式转换”：`image_wash2.py`](#2-重命名与真正格式转换image_wash2py)
  - [3) Excel 划分 Train/Test/Val：`image_fold_divid_by_train&test.py`](#3-excel-划分-traintestvalimage_fold_divid_by_traintestpy)
- [数据处理流程（配图）](#数据处理流程配图)
- [常见问题 FAQ](#常见问题-faq)
- [Roadmap](#roadmap)
- [致谢](#致谢)

---

## 项目简介

本仓库收录了影像数据规范化处理中的常用 GUI 小工具，用于：
- **批量将 DICOM 序列转换为 NIfTI（`*.nii.gz`）**，并可基于 DICOM MetaData 对患者/序列命名；
- **批量重命名与“真正格式转换”**（读取→写出，而非仅改后缀），统一 `.nii/.nii.gz/.mha/.nrrd` 等格式；
- **基于 Excel 切分数据集**，将 `root` 下的 case 目录复制/移动到 `train/test/validation`。

> 参考图示：**Fig. 1**（端到端流程）与 **Fig. 2**（三工具协同）。  
> 你可以将流程图放到 `docs/fig1.png` 与 `docs/fig2.png`，README 会自动引用。

---

## 安装与环境

- **Python**：3.8+
- **OS**：Windows / Linux / macOS（推荐 Windows 10/11）
- **依赖包**（按需）：
  ```bash
  pip install SimpleITK pydicom pandas openpyxl numpy

  
