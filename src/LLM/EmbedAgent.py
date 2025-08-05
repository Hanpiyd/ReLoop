import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
from tqdm import tqdm
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
import logging

from src.configs.config import (
    DEFAULT_EMBED_ONLINE_MODEL,
    DEFAULT_EMBED_LOCAL_MODEL,
    EMBED_REMOTE_URL,
    EMBED_TOKEN
)

logger = logging.getLogger(__name__)

class EmbedAgent:
    def __init__(self, token = EMBED_TOKEN, url = EMBED_REMOTE_URL):
        self.remote_url = url
        self.token = token
        self.header = {
            "Content-Type" : "application/json",
            "Authorization": f"Bearer {token}"
        }
        try:
            self.local_embedding_model = HuggingFaceEmbedding(
                model_name = DEFAULT_EMBED_ONLINE_MODEL
            )
        except Exception as e:
            logger.info("⚠加载远程嵌入模型失败，尝试加载本地嵌入模型！")
            self.local_embedding_model = HuggingFaceEmbedding(
                model_name = DEFAULT_EMBED_LOCAL_MODEL
            )
            
    def remote_embed(self, text:str, max_retry:int = 15, model:str = "BAAI/bge-m3"):
        url = self.remote_url
        json_data = json.dumps({
            "model" : model,
            "input" : text,
            "encoding_format" : "float"
        })
        try:
            response = requests.post(url, headers=self.header, data=json_data)
        except Exception as e:
            logger.error("请求失败")
            response = None
            for attempt in range(max_retry):
                try:
                    response = requests.post(url, headers=self.header, data=json_data)
                    if response.status_code == 200:
                        logger.info(f"第{attempt + 1}次重复请求成功")
                        break
                except Exception as e:
                    logger.error(f"第{attempt + 1}次重复请求失败")
                    response = None
                    
        if response is None or response.status_code != 200:
            return []
    
        try:
            res = response.json()
            embedding = res["data"][0]["embedding"]
            return embedding
        except json.JSONDecodeError as e:
            logger.error(f"JSON解码失败: {e}")
            return []
        
    
    def remote_embed_task(self, index, text):
        embedding = self.remote_embed(text)
        return index, embedding
    
    def batch_remote_embed(self, texts, workers:int = 10, desc: str = "Batch Embedding..."):
        embeddings = ["No Response"] * len(texts)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_l = [
                executor.submit(self.remote_embed_task, i, texts[i])
                for i in range(len(texts))
            ]
            for future in tqdm(
                as_completed(future_l),
                desc=desc,
                total=len(future_l),
                dynamic_ncols=True,
            ):
                i, embedding = future.result()
                embeddings[i] = embedding
                
        return embeddings
    
    def local_embed(self, text):
        embedding = self.local_embedding_model.get_text_embedding(text)
        return embedding
    
    def batch_local_embed(self, text_l):
        embed_documents = self.local_embedding_model.get_text_embedding_batch(
            text_l, show_progress=True
        )
        return embed_documents
            