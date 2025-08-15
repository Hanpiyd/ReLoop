import json
import sys
from pathlib import Path
import logging
from typing import List, Dict, Optional
import os

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import (
    BASE_DIR, 
    OUTPUT_DIR,
    TASK_DIRS,
    COARSE_GRAINED_TOPK,
    MIN_FILTERED_LIMIT
)
from src.LLM.ChatAgent import ChatAgent
from src.LLM.utils import load_prompt
from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.modules.utils import load_file_as_string, save_result, str2bool
from src.modules.feedback.feedback import FeedbackManager
from src.modules.preprocessor.data_filter import DataFilter
from src.modules.preprocessor.data_cleaner import DataCleaner
from src.modules.preprocessor.data_recaller import DataRecaller
from src.models.generator.outlines_generator import OutlinesGenerator
from src.models.generator.content_generator import ContentGenerator
from src.models.post_refine.post_refiner import PostRefiner
from src.models.generator.latex_generator import LatexGenerator

logger = logging.getLogger(__name__)

def parse_arguments_for_reloop():
    import argparse
    parser = argparse.ArgumentParser(description="重新执行文献综述生成流程")
    parser.add_argument("--task_id", type=str, default=None, help="任务ID")
    parser.add_argument("--keywords_add", type=str, default="", 
                       help="要添加的关键词，用逗号分隔")
    parser.add_argument("--keywords_remove", type=str, default="", 
                       help="要移除的关键词，用逗号分隔")
    parser.add_argument("--papers_keep", type=str, default="", 
                       help="要保留的论文ID，用逗号分隔")
    parser.add_argument("--papers_exclude", type=str, default="", 
                       help="要排除的论文ID，用逗号分隔")
    parser.add_argument("--topics_emphasize", type=str, default="", 
                       help="要强调的主题，用逗号分隔")
    parser.add_argument("--topics_reduce", type=str, default="", 
                       help="要减少关注的主题，用逗号分隔")
    parser.add_argument("--feedback_text", type=str, default="", 
                       help="一般性反馈文本")
    parser.add_argument("--skip_recall", action="store_true", 
                       help="跳过重新检索论文阶段")
    parser.add_argument("--gr", type=str2bool, nargs="?", const=False, default=False,
        help="whether to generate related work only instead of a whole survey.")
    parser.add_argument("--gp", type=str2bool, nargs="?", const=False, default=False,
        help="whether to generate a proposal instead of a survey.")
    
    return parser.parse_args()

def get_paper_ids(papers: List[Dict]) -> List[str]:
    return [paper.get("_id", "") for paper in papers if "_id" in paper]

def reloop_execution(task_id: Optional[str] = None, 
                    keywords_add: str = "",
                    keywords_remove: str = "",
                    papers_keep: str = "",
                    papers_exclude: str = "",
                    topics_emphasize: str = "",
                    topics_reduce: str = "",
                    feedback_text: str = "",
                    skip_recall: bool = False,
                    GENERATE_RELATED_WORK_ONLY:bool = False, 
                    GENERATE_PROPOSAL:bool = False):
    
    if task_id is None:
        task_id = load_latest_task_id()
        if task_id is None:
            logger.error("未找到最近的任务ID，请明确指定--task_id")
            return
        
    logger.info(f"准备重新执行任务: {task_id}")

    task_dir = ensure_task_dirs(task_id)
    config_path = task_dir / "tmp_config.json"
    try:
        config = json.loads(load_file_as_string(config_path))
        topic = config["topic"]
        key_words = config["key_words"]
        logger.info(f"成功加载任务配置: 主题='{topic}', 关键词='{key_words}'")
    except Exception as e:
        logger.error(f"加载任务配置失败: {e}")
        return
    
    feedback_manager = FeedbackManager(task_id=task_id)

    keywords_to_add = [k.strip() for k in keywords_add.split(",") if k.strip()]
    keywords_to_remove = [k.strip() for k in keywords_remove.split(",") if k.strip()]
    papers_to_keep = [p.strip() for p in papers_keep.split(",") if p.strip()]
    papers_to_exclude = [p.strip() for p in papers_exclude.split(",") if p.strip()]
    topics_to_emphasize = [t.strip() for t in topics_emphasize.split(",") if t.strip()]
    topics_to_reduce = [t.strip() for t in topics_reduce.split(",") if t.strip()]

    feedback_manager.add_user_feedback(
        keywords_to_add=keywords_to_add,
        keywords_to_remove=keywords_to_remove,
        papers_to_keep=papers_to_keep,
        papers_to_exclude=papers_to_exclude,
        topics_to_emphasize=topics_to_emphasize,
        topics_to_reduce=topics_to_reduce,
        feedback_text=feedback_text
    )

    iteration = feedback_manager.prepare_for_next_iteration()
    logger.info(f"开始执行第 {iteration} 轮迭代")

    chat_agent = ChatAgent()

    if not skip_recall:
        all_keywords = set(key_words.split(","))
        additional_keywords = feedback_manager.get_additional_keywords()
        keywords_to_remove = feedback_manager.get_keywords_to_remove()
        all_keywords.update(additional_keywords)
        all_keywords = all_keywords - set(keywords_to_remove)
        combined_keywords = ",".join(all_keywords)
        jsons_dir = task_dir / TASK_DIRS["JSONS_DIR"]
        papers_dir = task_dir / TASK_DIRS["PAPERS_DIR"]
        iteration_backup_dir = task_dir / "backups" / f"iteration_{iteration-1}"
        iteration_papers_dir = iteration_backup_dir / "papers"
        os.makedirs(iteration_papers_dir, exist_ok=True)
        import shutil
        for file in os.listdir(papers_dir):
            if file.endswith(".json"):
                shutil.copy(papers_dir / file, iteration_papers_dir / file)
        logger.info(f"使用关键词 '{combined_keywords}' 重新检索论文")
        time_s = config.get("time_s", "2017")
        time_e = config.get("time_e", "2024")
        page = "5"
        data_recaller = DataRecaller(topic=topic, chat_agent=chat_agent)
        previous_papers = feedback_manager.get_all_previous_papers()
        papers_to_exclude_set = feedback_manager.get_papers_to_exclude()
        papers = data_recaller._recall_papers_iterative(combined_keywords, page, time_s, time_e)
        papers = [p for p in papers if p.get("_id", "") not in papers_to_exclude_set]
        logger.info(f"开始过滤检索到的 {len(papers)} 篇论文")
        data_filter = DataFilter(papers=papers, chat_agent=chat_agent, feedback_manager=feedback_manager)
        filtered_papers = data_filter.run(topic, coarse_grained_topk=COARSE_GRAINED_TOPK)
        from src.modules.preprocessor.utils import save_papers
        save_papers(filtered_papers, jsons_dir)
        data_cleaner = DataCleaner()
        data_cleaner.run(task_id, chat_agent)

    logger.info("开始生成大纲")
    outline_generator = OutlinesGenerator(task_id)
    outline_generator.run(GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY, GENERATE_PROPOSAL=GENERATE_PROPOSAL)

    logger.info("开始生成内容")
    content_generator = ContentGenerator(task_id)
    content_generator.run(GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY, GENERATE_PROPOSAL=GENERATE_PROPOSAL)

    logger.info("开始内容优化")
    post_refiner = PostRefiner(task_id, chat_agent=chat_agent)
    post_refiner.run(GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY, GENERATE_PROPOSAL=GENERATE_PROPOSAL)

    if not (GENERATE_RELATED_WORK_ONLY or GENERATE_PROPOSAL):
        latex_generator = LatexGenerator(task_id)
        latex_generator.generate_full_survey()

    """ logger.info("开始生成LaTeX文档")
    latex_generator = LatexGenerator(task_id)
    latex_generator.generate_related_work_only()

    try:
        logger.info("开始编译PDF")
        latex_generator.compile_single_survey()
    except Exception as e:
        logger.error(f"PDF编译失败: {e}") """

    papers_dir = task_dir / TASK_DIRS["PAPERS_DIR"]
    paper_files = [f for f in os.listdir(papers_dir) if f.endswith('.json')]
    current_paper_ids = []

    for file in paper_files:
        try:
            paper_path = papers_dir / file
            paper_data = json.loads(load_file_as_string(paper_path))
            if "_id" in paper_data:
                current_paper_ids.append(paper_data["_id"])
        except Exception as e:
            logger.warning(f"读取论文 {file} 时出错: {e}")

    feedback_manager.save_current_iteration_papers(current_paper_ids)
    
    logger.info(f"第 {iteration} 轮迭代完成")
    logger.info(f"PDF输出位置: {task_dir}/survey.pdf")
    logger.info("如需进一步调整，请再次运行reloop.py并提供新的反馈")

if __name__ == "__main__":
    args = parse_arguments_for_reloop()
    reloop_execution(
        task_id=args.task_id,
        keywords_add=args.keywords_add,
        keywords_remove=args.keywords_remove,
        papers_keep=args.papers_keep,
        papers_exclude=args.papers_exclude,
        topics_emphasize=args.topics_emphasize,
        topics_reduce=args.topics_reduce,
        feedback_text=args.feedback_text,
        skip_recall=args.skip_recall,
        GENERATE_RELATED_WORK_ONLY=args.gr,
        GENERATE_PROPOSAL=args.gp
    )