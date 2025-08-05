import json
import math
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import List
import requests
from datetime import datetime
import openai
from llama_index.core import (
    Document,
    Settings,
    StorageContext,
    VectorStoreIndex,
    get_response_synthesizer,
    load_index_from_storage,
)
from llama_index.core.postprocessor import (
    KeywordNodePostprocessor,
    SimilarityPostprocessor,
)

from llama_index.core.node_parser import (
    SemanticSplitterNodeParser,
    SentenceWindowNodeParser,
    TokenTextSplitter,
    HierarchicalNodeParser,
)
from llama_index.core.prompts.base import PromptTemplate
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai import OpenAI
from llama_index.core import (
    load_index_from_storage,
    load_indices_from_storage,
    load_graph_from_storage,
)
from llama_index.embeddings.openai import OpenAIEmbedding
from tqdm import tqdm
import logging

from src.configs.config import(
    BASE_DIR,
    OUTPUT_DIR,
    DEFAULT_EMBED_LOCAL_MODEL,
    DEFAULT_EMBED_ONLINE_MODEL,
    DEFAULT_LLAMAINDEX_OPENAI_MODEL,
    TOKEN,
    REMOTE_URL,
    SPLITTER_CHUNK_SIZE,
    SPLITTER_WINDOW_SIZE,
    DEFAULT_SPLITTER_TYPE,
    TASK_DIRS
)

from src.configs.utils import ensure_task_dirs

logger = logging.getLogger(__name__)

sys.path.append(BASE_DIR)

class LlamaIndexWrapper:
    Api_key = TOKEN
    Api_base = REMOTE_URL
    def __init__(self, task_id=None, embed_model:str = None, llm_model:str = None):
        self.task_id = task_id
        
        if task_id:
            self.task_dir = ensure_task_dirs(task_id)
            self.vector_index_dir = self.task_dir / "vector_indexes"
        else:
            self.vector_index_dir = Path(f"{OUTPUT_DIR}/vector_indexes")
            
        self.vector_index_dir.mkdir(parents=True, exist_ok=True)
        
        if embed_model is None:
            try:
                Settings.embed_model = HuggingFaceEmbedding(
                    model_name = DEFAULT_EMBED_ONLINE_MODEL
                )
            except Exception as e:
                logger.info(
                    f"{e}\nFailed to load embedding model {DEFAULT_EMBED_ONLINE_MODEL}, try to use local model {DEFAULT_EMBED_LOCAL_MODEL}."
                )
                Settings.embed_model = HuggingFaceEmbedding(
                    model_name=DEFAULT_EMBED_LOCAL_MODEL
                )
        logger.debug("model loaded successfully.")
        self.embed_model = Settings.embed_model
        Settings.llm = llm_model
        self.index = None
        self.retriever = None
        self.query_engine = None
        self.splitter_type = DEFAULT_SPLITTER_TYPE
        self.split_parser = None
        self.splitter_window_size = SPLITTER_WINDOW_SIZE
        self.splitter_buffer_size = 1
        self.splitter_breakpoint_percentile_threshold = 95
        self.splitter_chunk_size = SPLITTER_CHUNK_SIZE
        self.splitter_chunk_overlap = 20
        self.splitter_token_separator = " "
        self.init_split_parser()
        self.insert_batch_size = 2048
        
    @classmethod
    def get_openai_llm(cls, model = DEFAULT_LLAMAINDEX_OPENAI_MODEL, temp=0):
        return OpenAI(model = model, temperature = temp, api_base = cls.Api_base, api_key = cls.Api_key)
    
    def init_split_parser(self):
        if self.splitter_type == "sentence":
            self.split_parser = SentenceWindowNodeParser.from_defaults(
                window_size = self.splitter_window_size,
                window_metadata_key = "window",
                original_text_metadata_key="original_sentence"
            )
        elif self.splitter_type == "semantic":
            self.split_parser = SemanticSplitterNodeParser(
                buffer_size=self.splitter_buffer_size,
                breakpoint_percentile_threshold=self.splitter_breakpoint_percentile_threshold,
                embed_model=self.embed_model
            )
        elif self.splitter_type == "token":
            self.split_parser = TokenTextSplitter(
                chunk_size = self.splitter_chunk_size,
                chunk_overlap=self.splitter_chunk_overlap,
                separator=self.splitter_token_separator
            )
        elif self.splitter_type == "hierarchical":
            self.split_parser = HierarchicalNodeParser.from_defaults(
                chunk_sizes=[2048, 512, 128]
            )
        else:
            raise ValueError()
        
    def create_vector_index(self, nodes, store_local = False):
        start_time = datetime.now()
        if store_local and self.vector_index_dir.exists() and len(os.listdir(self.vector_index_dir)) > 0:
            logger.info(f"loading index from {self.vector_index_dir} ......")
            self.load_vector_index(vector_index_dir=self.vector_index_dir)
        else:
            logger.info(f"Creating VectorStoreIndex ......")
            if isinstance(nodes[0], Document):
                self.index = VectorStoreIndex.from_documents(
                    nodes, show_process = True, insert_batch_size = self.insert_batch_size
                )
            else:
                self.index = VectorStoreIndex(
                    nodes = nodes, show_progress=True, insert_batch_size=self.insert_batch_size
                )
            self.query_engine = self.index.as_query_engine()
            if store_local:
                self.vector_index_dir.mkdir(parents=True, exist_ok=True)
                self.index.storage_context.persist(persist_dir=self.vector_index_dir)
        end_time = datetime.now()
        elapsed_time = end_time - start_time
        hours, remainder = divmod(elapsed_time.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        logger.info(f"Create_vector_index took {int(hours)} hours {int(minutes)} minutes {int(seconds)} seconds")
        return self.index
    
    def load_vector_index(self, vector_index_dir = None):
        if vector_index_dir is None:
            vector_index_dir = self.vector_index_dir
        storage_context = StorageContext.from_defaults(persist_dir = vector_index_dir)
        self.index = load_index_from_storage(storage_context)
        
    def get_retriever(self, index, top_k = 10):
        if index is None:
            index = self.index
        self.retriever = VectorIndexRetriever(index=index, similarity_top_k = top_k)
        return self.retriever
    
    def get_simple_query_engine(self, index: VectorStoreIndex, top_k = 10):
        if index is None:
            index = self.index
        self.retriever = self.get_retriever(index, top_k)
        response_synthesizer = get_response_synthesizer()
        node_postprocessors = [SimilarityPostprocessor(similarity_cutoff=0.3)]
        self.query_engine = RetrieverQueryEngine(
            retriever = self.retriever,
            response_synthesizer = response_synthesizer,
            node_postprocessors = node_postprocessors
        )
        return self.query_engine
        
    def question_answer(self, query):
        response = self.query_engine.query(query)
        return response
        
        