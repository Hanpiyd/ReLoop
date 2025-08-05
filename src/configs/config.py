from pathlib import Path

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent.parent
OUTPUT_DIR = Path(f"{BASE_DIR}/outputs")
CACHE_DIR = Path(f"{BASE_DIR}/cache")
DATASET_DIR = Path(f"{BASE_DIR}/datasets")
PAPERS_DIR = Path(f"{BASE_DIR}/papers")
AVAILABLE_DATA_SOURCES = ["google_scholar", "arxiv"]
DEFAULT_DATA_FETCHER_ENABLE_CACHE = True
DEFAULT_ITERATION_LIMIT = 3
DEFAULT_PAPER_POOL_LIMIT = 1000
CUT_WORD_LENGTH = 10
COARSE_GRAINED_TOPK = 200
MIN_FILTERED_LIMIT = 150
DEFAULT_LLAMAINDEX_OPENAI_MODEL = "openai/gpt-4o-mini"
SPLITTER_CHUNK_SIZE = 2048
SPLITTER_WINDOW_SIZE = 6
DEFAULT_SPLITTER_TYPE = "sentence"
MD_TEXT_LENGTH = 20000
ADVANCED_CHATAGENT_MODEL = "openai/gpt-4o-mini"
RESOURCE_DIR = Path(f"{BASE_DIR}/resources")
FEEDBACK_DIR = "feedback"
MAX_ITERATION = 5
PAPER_SIMILARITY_BOOST = 1.2
SERPAPI_API_KEY = ""

GENERATE_ONLY_RELATED_WORK = True
RELATED_WORK_SECTION_TITLE = "Related Work" 
RELATED_WORK_DESCRIPTION = "A comprehensive overview of existing research in this area"

TASK_DIRS = {
    "JSONS_DIR": "jsons",      
    "PAPERS_DIR": "papers",   
    "LATEX_DIR": "latex",      
    "TMP_DIR": "tmp"           
}

MAINBODY_FILES = {
    "INITIAL": "mainbody_initial.tex",    
    "RAG": "mainbody_rag.tex",             
    "REWRITTEN": "mainbody_rewritten.tex", 
    "RULE": "mainbody_rule.tex",           
    "FINAL": "mainbody_final.tex",
    "RELATED_WORK": "related_work.tex"   
}

# EmbedAgent.py
DEFAULT_EMBED_ONLINE_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_EMBED_LOCAL_MODEL = ""
EMBED_REMOTE_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBED_TOKEN = ""

# ChatAgent.py
REMOTE_URL = "https://openrouter.ai/api/v1/chat/completions"
LOCAL_URL = ""
TOKEN = ""
DEFAULT_CHATAGENT_MODEL = "openai/gpt-4o-mini"
CHAT_AGENT_WORKERS = 4
