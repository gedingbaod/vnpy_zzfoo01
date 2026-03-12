r"""
掘金数据解压模块
用于解压 G:\掘金数据\2026\ 目录下的 rar 压缩包到 G:\掘金数据\load\
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JuejinDataExtractor:
    """掘金数据解压器"""

    def __init__(
        self,
        source_dir: str = r"G:\掘金数据\2026",
        target_dir: str = r"G:\掘金数据\load"
    ):
        """
        初始化解压器

        Args:
            source_dir: 压缩包所在目录
            target_dir: 解压目标目录
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)

        # 确保目标目录存在
        self.target_dir.mkdir(parents=True, exist_ok=True)

    def get_rar_files(self) -> List[Path]:
        """
        获取所有 rar 压缩包

        Returns:
            rar 文件路径列表
        """
        rar_files = list(self.source_dir.glob("*.rar"))
        rar_files.sort()
        return rar_files

    def extract_with_7zip(self, rar_file: Path, extract_to: Path) -> bool:
        """
        使用 7-Zip 命令解压 (Windows 推荐)

        Args:
            rar_file: rar 文件路径
            extract_to: 解压目标路径

        Returns:
            是否解压成功
        """
        # 7-Zip 常见安装路径
        seven_zip_paths = [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ]

        seven_zip = None
        for path in seven_zip_paths:
            if Path(path).exists():
                seven_zip = path
                break

        if not seven_zip:
            logger.warning("7-Zip 未找到，请安装: https://www.7-zip.org/")
            return False

        try:
            # 使用 7z x 命令解压（保持目录结构）
            # -o 后面紧跟输出路径（无空格）
            extract_arg = f"-o{extract_to}"
            result = subprocess.run(
                [seven_zip, 'x', '-y', str(rar_file), extract_arg],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            if result.returncode == 0:
                return True
            else:
                logger.warning(f"7-Zip 解压返回错误: {result.returncode}")
                return False
        except Exception as e:
            logger.warning(f"7-Zip 解压异常: {e}")
            return False

    def extract_with_patoolib(self, rar_file: Path, extract_to: Path) -> bool:
        """
        使用 patoolib 库解压

        Args:
            rar_file: rar 文件路径
            extract_to: 解压目标路径

        Returns:
            是否解压成功
        """
        try:
            import patoolib
            patoolib.extract_archive(str(rar_file), outdir=str(extract_to))
            return True
        except ImportError:
            logger.warning("patoolib 未安装，请运行: pip install patoolib")
            return False
        except Exception as e:
            logger.error(f"patoolib 解压失败: {e}")
            return False

    def extract_with_rarfile(self, rar_file: Path, extract_to: Path) -> bool:
        """
        使用 rarfile 库解压

        Args:
            rar_file: rar 文件路径
            extract_to: 解压目标路径

        Returns:
            是否解压成功
        """
        try:
            import rarfile
            with rarfile.RarFile(str(rar_file)) as rf:
                rf.extractall(path=str(extract_to))
            return True
        except ImportError:
            logger.warning("rarfile 未安装，请运行: pip install rarfile")
            return False
        except Exception as e:
            logger.error(f"rarfile 解压失败: {e}")
            return False

    def extract_rar(self, rar_file: Path) -> Optional[Path]:
        """
        解压单个 rar 文件

        Args:
            rar_file: rar 文件路径

        Returns:
            解压后的目录路径，失败返回 None
        """
        extract_to = self.target_dir / rar_file.stem

        # 如果已解压，跳过
        if extract_to.exists():
            logger.info(f"已存在，跳过: {extract_to.name}")
            return extract_to

        logger.info(f"开始解压: {rar_file.name}")

        # 尝试多种解压方式
        methods = [
            self.extract_with_7zip,
            self.extract_with_patoolib,
            self.extract_with_rarfile
        ]

        for method in methods:
            if method(rar_file, extract_to):
                logger.info(f"解压成功: {rar_file.name} -> {extract_to.name}")
                return extract_to

        logger.error(f"解压失败: {rar_file.name}")
        return None

    def extract_all(self, pattern: str = "juejin_data_*.rar") -> List[Path]:
        """
        解压所有匹配的 rar 文件

        Args:
            pattern: 文件匹配模式

        Returns:
            成功解压的目录路径列表
        """
        rar_files = list(self.source_dir.glob(pattern))
        rar_files.sort()

        logger.info(f"找到 {len(rar_files)} 个压缩包")

        extracted_dirs = []
        for i, rar_file in enumerate(rar_files, 1):
            logger.info(f"[{i}/{len(rar_files)}] 处理: {rar_file.name}")
            result = self.extract_rar(rar_file)
            if result:
                extracted_dirs.append(result)

        logger.info(f"解压完成，成功 {len(extracted_dirs)}/{len(rar_files)}")
        return extracted_dirs

    def extract_by_date(self, date_str: str) -> Optional[Path]:
        """
        解压指定日期的压缩包

        Args:
            date_str: 日期字符串，如 "2026-01-22"

        Returns:
            解压后的目录路径，失败返回 None
        """
        rar_file = self.source_dir / f"juejin_data_{date_str}.rar"
        if not rar_file.exists():
            logger.error(f"文件不存在: {rar_file}")
            return None

        return self.extract_rar(rar_file)

    def get_csv_files(self, extracted_dir: Path) -> List[Path]:
        """
        获取解压后的 CSV 文件

        Args:
            extracted_dir: 解压目录

        Returns:
            CSV 文件路径列表
        """
        if not extracted_dir.exists():
            return []

        csv_files = list(extracted_dir.rglob("*.csv"))
        return csv_files


def main():
    """主函数 - 示例用法"""

    extractor = JuejinDataExtractor()

    # 方式1: 解压指定日期的压缩包
    extractor.extract_by_date("2026-01-21")

    # 方式2: 解压所有压缩包
    # extracted_dirs = extractor.extract_all()

    # 查看解压后的 CSV 文件
    # for dir_path in extracted_dirs[:3]:  # 只显示前3个
    #     csv_files = extractor.get_csv_files(dir_path)
    #     print(f"\n{dir_path.name}:")
    #     for csv_file in csv_files[:5]:  # 每个目录显示前5个
    #         print(f"  - {csv_file.relative_to(dir_path)}")


if __name__ == "__main__":
    main()
