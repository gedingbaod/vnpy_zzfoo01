"""
掘金数据导入流程控制器
1. 解压 rar 压缩包
2. 找到 TickData 文件夹
3. 导入 CSV 数据到 DolphinDB
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
# 添加 data_load 目录到 Python 路径
data_load_dir = Path(__file__).parent
sys.path.insert(0, str(data_load_dir))

import logging
from unrar_juejin_data import JuejinDataExtractor
from import_juejin_csv_to_dolphindb import import_directory

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JuejinImportPipeline:
    """掘金数据导入流水线"""

    def __init__(
        self,
        source_dir: str = r"G:\掘金数据\2026",
        target_dir: str = r"G:\掘金数据\load",
    ):
        """
        初始化流水线

        Args:
            source_dir: 压缩包所在目录
            target_dir: 解压目标目录
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.extractor = JuejinDataExtractor(source_dir, target_dir)

    def import_single_date(self, date_str: str, chunk_size: int = 10000) -> dict:
        """
        导入单个日期的数据

        Args:
            date_str: 日期字符串，如 "2026-01-21"
            chunk_size: 每批次导入的数据量

        Returns:
            导入结果统计
        """
        logger.info(f"=" * 60)
        logger.info(f"开始处理日期: {date_str}")
        logger.info(f"=" * 60)

        # 步骤1: 解压压缩包
        logger.info(f"[步骤1/3] 解压压缩包...")
        extracted_dir = self.extractor.extract_by_date(date_str)
        if not extracted_dir:
            logger.error(f"解压失败: {date_str}")
            return {"status": "failed", "reason": "解压失败"}

        # 步骤2: 查找 TickData 文件夹
        logger.info(f"[步骤2/3] 查找 TickData 文件夹...")
        tickdata_dir = extracted_dir / date_str / "TickData"
        if not tickdata_dir.exists():
            # 尝试其他可能的路径
            tickdata_dir = None
            for subdir in extracted_dir.rglob("TickData"):
                tickdata_dir = subdir
                break

        if not tickdata_dir or not tickdata_dir.exists():
            logger.error(f"未找到 TickData 文件夹")
            return {"status": "failed", "reason": "未找到 TickData 文件夹"}

        logger.info(f"找到 TickData 文件夹: {tickdata_dir}")

        # 统计 CSV 文件数量
        csv_files = list(tickdata_dir.glob("*.csv"))
        logger.info(f"CSV 文件数量: {len(csv_files)}")

        # 步骤3: 导入数据
        logger.info(f"[步骤3/3] 导入数据到数据库...")
        results = import_directory(tickdata_dir, pattern="*.csv", chunk_size=chunk_size)

        # 统计结果
        success_count = sum(1 for count in results.values() if count > 0)
        total_ticks = sum(results.values())

        logger.info(f"=" * 60)
        logger.info(f"处理完成: {date_str}")
        logger.info(f"  成功: {success_count}/{len(results)} 个文件")
        logger.info(f"  总 tick 数: {total_ticks}")
        logger.info(f"=" * 60)

        return {
            "status": "success",
            "date": date_str,
            "success_files": success_count,
            "total_files": len(results),
            "total_ticks": total_ticks,
            "details": results,
        }

    def import_all(self, chunk_size: int = 10000) -> list[dict]:
        """
        导入所有日期的数据

        Args:
            chunk_size: 每批次导入的数据量

        Returns:
            每个日期的导入结果列表
        """
        rar_files = self.extractor.get_rar_files()
        logger.info(f"找到 {len(rar_files)} 个压缩包")

        # 从文件名提取日期
        dates = []
        for rar_file in rar_files:
            # 文件名格式: juejin_data_2026-01-21.rar
            parts = rar_file.stem.split('_')
            if len(parts) >= 3:
                date_str = parts[2]  # 2026-01-21
                dates.append(date_str)

        logger.info(f"待处理日期: {dates}")

        results = []
        for date_str in dates:
            try:
                result = self.import_single_date(date_str, chunk_size)
                results.append(result)
            except Exception as e:
                logger.error(f"处理 {date_str} 失败: {e}")
                results.append({
                    "status": "failed",
                    "date": date_str,
                    "reason": str(e),
                })

        return results


def main():
    """主函数"""
    pipeline = JuejinImportPipeline()

    # 先测试单个日期
    result = pipeline.import_single_date("2026-01-21", chunk_size=10000)

    if result["status"] == "success":
        print("\n导入成功！")
        print(f"  成功文件: {result['success_files']}/{result['total_files']}")
        print(f"  总 tick 数: {result['total_ticks']}")
    else:
        print(f"\n导入失败: {result.get('reason', '未知错误')}")

    # 确认后导入所有数据
    # confirm = input("\n是否继续导入所有数据? (y/n): ")
    # if confirm.lower() == 'y':
    #     results = pipeline.import_all()
    #     print("\n全部导入完成!")
    #     for r in results:
    #         print(f"  {r['date']}: {r.get('status', 'unknown')}")


if __name__ == "__main__":
    main()
