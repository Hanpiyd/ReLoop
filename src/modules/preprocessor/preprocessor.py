import sys
from pathlib import Path
import logging
import json
import re
import os

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import (
    COARSE_GRAINED_TOPK,
    OUTPUT_DIR,
    TASK_DIRS,
    BASE_DIR
)

from src.configs.utils import ensure_task_dirs
from src.LLM.ChatAgent import ChatAgent
from src.LLM.utils import load_prompt
from src.modules.preprocessor.data_recaller import DataRecaller
from src.modules.preprocessor.data_cleaner import DataCleaner
from src.modules.preprocessor.data_filter import DataFilter
from src.modules.preprocessor.pdf_processor import process_pdf_files_with_mineru
from src.modules.preprocessor.utils import(
    ArgsNamespace,
    create_tmp_config,
    parse_arguments_for_preprocessor,
    save_papers,
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def process_pdf_files(pdf_paths, topic):
    if isinstance(pdf_paths, str):
        pdf_paths = [pdf_paths]

    if not pdf_paths:
        return []
    
    logger.info(f"使用新的PDF处理逻辑处理 {len(pdf_paths)} 个PDF文件...")
    output_dir = f"{BASE_DIR}/mineru_output"
    download_dir = f"{BASE_DIR}/mineru_download"
    processed_papers = process_pdf_files_with_mineru(pdf_paths, topic, output_dir, download_dir)
    return processed_papers

def generate_initial_keywords(topic, pdf_papers=None, chat_agent=None, description=None):
    if not pdf_papers and not description:
        return None
    
    if chat_agent is None:
        chat_agent = ChatAgent()

    paper_summaries = ""
    if pdf_papers:
        paper_summaries = "\n\n".join([
            f"Paper: {paper.get('title', 'Unknown')}\n{paper.get('summary', '')}" 
            for paper in pdf_papers
        ])

    prompt_params = {
        "topic": topic,
        "paper_summaries": paper_summaries if pdf_papers else "No reference papers provided."
    }

    if description:
        prompt_params["description"] = description

    prompt_template = "generate_initial_keywords_with_description.md" if description else "generate_initial_keywords.md"

    prompt = load_prompt(
        f"{BASE_DIR}/resources/LLM/prompts/preprocessor/{prompt_template}",
        **prompt_params
    )   

    response = chat_agent.remote_chat(prompt)
    keywords_match = re.search(r'<Answer>(.*?)</Answer>', response, re.DOTALL)
    if keywords_match:
        keywords = keywords_match.group(1).strip()
        source = []
        if pdf_papers:
            source.append("PDF content")
        if description:
            source.append("user description")
        
        logger.info(f"Generated initial keywords based on {' and '.join(source)}: {keywords}")
        return keywords
    else:
        logger.warning("Failed to extract keywords from response")
        return None

def single_preprocessing(args:ArgsNamespace):
    chat_agent = ChatAgent()
    pdf_papers = []
    tmp_config = create_tmp_config(args.title, args.key_words)
    topic = tmp_config["topic"]
    task_id = tmp_config["task_id"]
    
    description = None
    if hasattr(args, 'description') and args.description:
        description = args.description
        tmp_config["description"] = description
        config_path = Path(OUTPUT_DIR) / task_id / "tmp_config.json"
        with open(config_path, 'w') as f:
            json.dump(tmp_config, f, indent=4)

    has_pdf = False
    if hasattr(args, 'pdf_paths') and args.pdf_paths:
        has_pdf = True
        pdf_papers = process_pdf_files(args.pdf_paths, topic)

    if has_pdf or description:
        initial_keywords = generate_initial_keywords(topic, pdf_papers, chat_agent, description)
        if initial_keywords:
            if args.key_words:
                combined_keywords = f"{args.key_words},{initial_keywords}"
            else:
                combined_keywords = initial_keywords     
            tmp_config["key_words"] = combined_keywords
            config_path = Path(OUTPUT_DIR) / task_id / "tmp_config.json"
            with open(config_path, 'w') as f:
                json.dump(tmp_config, f, indent=4)
            topic = tmp_config["topic"]

    task_dir = ensure_task_dirs(task_id)
    jsons_dir = task_dir / TASK_DIRS["JSONS_DIR"]

    logger.debug("Go to Search Papers")
    recaller = DataRecaller(topic, enable_cache=args.enable_cache, chat_agent=chat_agent)
    recalled_papers = recaller._recall_papers_iterative(tmp_config["key_words"], args.page, args.time_s, args.time_e)

    if pdf_papers:
        for paper in pdf_papers:
            if "_id" not in paper:
                paper["_id"] = f"pdf_{paper.get('bib_name', hash(paper.get('title', '')))}"
            if "from" not in paper:
                paper["from"] = "pdf"
        recalled_papers.extend(pdf_papers)
        logger.info(f"添加 {len(pdf_papers)} 个PDF论文到检索列表。总论文数: {len(recalled_papers)}")
    
    logger.info(
        f"================= 总共检索到 {len(recalled_papers)} 篇论文 =================="
    )
    
    filter = DataFilter(recalled_papers, chat_agent)
    filtered_papers = filter.run(topic, coarse_grained_topk=COARSE_GRAINED_TOPK)
    
    if pdf_papers:
        pdf_ids = {paper["_id"] for paper in pdf_papers}
        filtered_ids = {paper["_id"] for paper in filtered_papers}
        missing_pdfs = [paper for paper in pdf_papers if paper["_id"] not in filtered_ids]
        if missing_pdfs:
            logger.info(f"将 {len(missing_pdfs)} 篇PDF论文添加回过滤列表，因为它们是必需的参考文献。")
            filtered_papers.extend(missing_pdfs)
    
    logger.info(
        f"================= 过滤后总共保存了 {len(filtered_papers)} 篇论文 =================="
    )
    save_papers(filtered_papers, jsons_dir)
    cleaner = DataCleaner(papers=[])
    cleaner.run(task_id, chat_agent)
    return task_id