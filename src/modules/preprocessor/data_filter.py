import json
import os
import re
from pathlib import Path
from typing import Union, List, Set, Optional
from llama_index.core import Document
from llama_index.core.schema import NodeWithScore
from tqdm import tqdm
import logging

from src.configs.config import(
    BASE_DIR,
    COARSE_GRAINED_TOPK,
    MIN_FILTERED_LIMIT
)

from src.LLM.ChatAgent import ChatAgent
from src.LLM.utils import load_prompt
from src.modules.utils import load_file_as_string
from src.models.rag.modeling_llamaidx import LlamaIndexWrapper
from src.modules.feedback.feedback import FeedbackManager

logger = logging.getLogger()

class DataFilter:
    def __init__(self, papers, chat_agent:ChatAgent = None, feedback_manager: Optional[FeedbackManager] = None):
        self.papers = papers
        self.embed_agent = LlamaIndexWrapper()
        self.chat_agent = chat_agent if chat_agent is not None else ChatAgent()
        self.feedback_manager = feedback_manager
        
    @staticmethod
    def from_saved(dir_path, chat_agent = None, feedback_manager=None):
        chat_agent if chat_agent is not None else ChatAgent()
        papers = []
        for f in os.listdir(dir_path):
            if not f.endswith(".json"):
                continue
            p = Path(dir_path) / f
            papers.append(json.loads(load_file_as_string(p)))
        logger.debug(f"Load {len(papers)} papers from saved dir: {dir_path}")
        return DataFilter(papers=papers, chat_agent=chat_agent, feedback_manager=feedback_manager)
    
    def create_index(self):
        docs = []
        for i, paper in tqdm(enumerate(self.papers)):
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            doc_for_llamaindex = Document(
                text = title + abstract, metadata = {"title": title, "index" : i}
            )
            docs.append(doc_for_llamaindex)
        
        logger.debug(f"==== Creating index of {len(self.papers)} papers.=======")
        self.embed_agent.create_vector_index(nodes=docs, store_local=False)
        
    def get_top_similarity(self, topic:str, top_k:int = 300):
        retriver = self.embed_agent.get_retriever(self.embed_agent.index, top_k)
        result = retriver.retrieve(topic)
        return result
    
    def coarse_grained_sort(self, topic: str, topk: int = 300):
        self.create_index()
        nodes = self.get_top_similarity(topic, topk)
        papers = []
        for node in nodes:
            paper = self.papers[node.metadata["index"]]
            paper["similarity score"] = node.score
            papers.append(paper)
        return papers
    
    def fine_grained_sort(self, papers, topic:str, min_limit:int = 100):
        extract_content = lambda text: re.findall(
            r"<Answer>(.*?)</Answer>", text, re.DOTALL
        )[0]
        prompt_path = Path(
            f"{BASE_DIR}/resources/LLM/prompts/preprocessor/judge_relevance.md"
        )
        prompts = [
            load_prompt(prompt_path, Abstract=paper["abstract"], Topic=topic)
            for paper in papers
        ]
        responses = self.chat_agent.batch_remote_chat(
            prompt_l=prompts, desc="batch_remote_chat for fine grained sorting..."
        )
        sorted_papers = []
        for res, paper in zip(responses, papers):
            try:
                ans = extract_content(res)
            except Exception as e:
                ans = ""
                logger.error(
                    f"Error occurs when dealing with gpt's response. Error: {str(e)}. Response: {res}"
                )
            if "1" in ans:
                sorted_papers.append(paper)
        return sorted_papers
    
    def apply_user_feedback(self, papers: List[dict]) -> List[dict]:
        if not self.feedback_manager:
            return papers
        
        papers_to_keep = self.feedback_manager.get_papers_to_keep()
        papers_to_exclude = self.feedback_manager.get_papers_to_exclude()
        emphasized_topics = self.feedback_manager.get_emphasized_topics()

        result_papers = []

        for paper in papers:
            paper_id = paper.get("_id", "")
            if paper_id in papers_to_exclude:
                continue
            if paper_id in papers_to_keep:
                result_papers.append(paper)
                continue
            if emphasized_topics:
                title = paper.get("title", "").lower()
                abstract = paper.get("abstract", "").lower()
                for topic in emphasized_topics:
                    topic_lower = topic.lower()
                    if topic_lower in title or topic_lower in abstract:
                        if "similarity_score" in paper:
                            paper["similarity_score"] *= 1.2
                        break
            result_papers.append(paper)

        if result_papers and "similarity_score" in result_papers[0]:
            result_papers.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
        return result_papers
    
    def run(self, topic:str, coarse_grained_topk: int = COARSE_GRAINED_TOPK, min_limit: int = MIN_FILTERED_LIMIT):
        if self.feedback_manager:
            papers_to_exclude = self.feedback_manager.get_papers_to_exclude()
            self.papers = [p for p in self.papers if p.get("_id", "") not in papers_to_exclude]

        coarse_grained_papers = self.coarse_grained_sort(topic, coarse_grained_topk)
        logger.info(
            f"=========== {len(coarse_grained_papers)} left after coarse_grained ==========="
        )
        fine_grained_papers = self.fine_grained_sort(coarse_grained_papers, topic, min_limit)
        logger.info(
            f"=========== {len(fine_grained_papers)} left after fine_grained ==========="
        )
        if self.feedback_manager:
            fine_grained_papers = self.apply_user_feedback(fine_grained_papers)
            logger.info(
                f"=========== {len(fine_grained_papers)} left after applying user feedback ==========="
            )
        if len(fine_grained_papers) < min_limit and self.feedback_manager:
            papers_to_keep = self.feedback_manager.get_papers_to_keep()
            papers_to_keep_ids = set(papers_to_keep)
            included_keep_papers = [p for p in fine_grained_papers if p.get("_id", "") in papers_to_keep_ids]
            included_keep_ids = {p.get("_id", "") for p in included_keep_papers}
            missing_keep_ids = papers_to_keep_ids - included_keep_ids
            if missing_keep_ids:
                for paper in self.papers:
                    if paper.get("_id", "") in missing_keep_ids and paper not in fine_grained_papers:
                        fine_grained_papers.append(paper)
                        
            logger.info(
                f"=========== {len(fine_grained_papers)} after ensuring minimum papers ==========="
            )
        return fine_grained_papers
