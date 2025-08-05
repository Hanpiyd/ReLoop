import fcntl
import requests
import json
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm
from pathlib import Path
import logging

from src.configs.config import (
    REMOTE_URL,
    LOCAL_URL,
    TOKEN,
    BASE_DIR,
    OUTPUT_DIR,
    DEFAULT_CHATAGENT_MODEL,
    CHAT_AGENT_WORKERS
)

logger = logging.getLogger(__name__)



class ChatAgent:
    def __init__(self, token:str = TOKEN, remote_url:str = REMOTE_URL, local_url:str = LOCAL_URL, files_url:str = None):
        self.remote_url = remote_url
        self.token = token
        self.local_url = local_url
        self.files_url = files_url if files_url is not None else "https://api.openai.com/v1/files"
        self.header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self.batch_workers = CHAT_AGENT_WORKERS
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def upload_file(self, pdf_path, purpose="assistants"):
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            with open(pdf_path, "rb") as f:
                files = {"file": (os.path.basename(pdf_path), f, "application/pdf")}
                data = {"purpose": purpose}
                response = requests.post(
                    self.files_url,
                    headers=headers,
                    files=files,
                    data=data
                )
                response.raise_for_status()
                file_data = response.json()
                logger.info(f"Successfully uploaded PDF: {pdf_path}, file_id: {file_data['id']}")
                return file_data["id"]
        except Exception as e:
            logger.error(f"Error uploading PDF {pdf_path}: {e}")
            raise
    
        
    @retry(
        stop=stop_after_attempt(30),
        wait=wait_exponential(min=1, max=300),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def remote_chat(self, text_content, temperature:float = 0.5, model = DEFAULT_CHATAGENT_MODEL, pdf_paths=None):
        url = self.remote_url
        header = self.header
        message = [{
            "role" : "user",
            "content" : text_content
        }]
        payload = {
            "model" : model,
            "messages" : message,
            "temperature" : temperature
        }
        if pdf_paths:
            files_id = []
            if isinstance(pdf_paths, str):
                pdf_paths = [pdf_paths]
            for pdf_path in pdf_paths:
                file_id = self.upload_file(pdf_path)
                files_id.append(file_id)
            if files_id:
                payload["file_ids"] = files_id
        
        response = requests.post(url, headers=header, json=payload)
        if response.status_code != 200:
            logger.error(f"chat response code: {response.status_code}\n{response.text[:500]}, retrying...")
            response.raise_for_status()
        try:
            res = json.loads(response.text)
            res_text = res["choices"][0]["message"]["content"]
        except Exception as e:
            res_text = f"Error: {e}"
            logger.error(f"There is an error: {e}")
        return res_text
    
    def _remote_chat(self, index, content, temperature:float = 0.5, model=DEFAULT_CHATAGENT_MODEL, pdf_paths=None):
        return index, self.remote_chat(content, temperature, model, pdf_paths)
    
    def batch_remote_chat(self, prompt_l, desc: str = "batch_chating...", workers:int = CHAT_AGENT_WORKERS, temperature:float = 0.5, pdf_paths_list=None):
        if workers is None:
            workers = self.batch_workers
        if pdf_paths_list is None:
            pdf_paths_list = [None] * len(prompt_l)
        elif len(pdf_paths_list) != len(prompt_l):
            raise ValueError("pdf_paths_list长度必须与prompt_l相同")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_l = [
                executor.submit(self._remote_chat, i, prompt_l[i], temperature, DEFAULT_CHATAGENT_MODEL, pdf_paths_list[i])
                for i in range(len(prompt_l))
            ]
            res_l = ["No Response"] * len(prompt_l)
            for future in tqdm(
                as_completed(future_l),
                desc=desc,
                total=len(future_l),
                dynamic_ncols=True,
            ):
                i, resp = future.result()
                res_l[i] = resp
        return res_l
    
    def local_chat(self, query):
        query = """
            <|begin_of_text|><|start_header_id|>system<|end_header_id|>
            You are a helpful AI assistant.<|eot_id|><|start_header_id|>user<|end_header_id|>
            {}<|eot_id|><|start_header_id|>assistant<|end_header_id|>""".format(query)
        payload = json.dumps(
            {
                "prompt" : query,
                "temperature" : 1.0,
                "max_token" : 102400,
                "n" : 1
            }
        )
        headers = {
            "Content-Type": "application/json"
        }
        res = requests.request("POST", self.local_url, headers=headers, data=payload)
        if res.status_code != 200:
            logger.info("chat response code: {}".format(res.status_code), query[:20])
            return "chat response code: {}".format(res.status_code)
        return res.json()["text"][0].replace(query, "")
    
    def _local_chat(self, index, query):
        return index, self.local_chat(query)
    
    def batch_local_chat(self, query_l, worker=16, desc="bach local inferencing..."):
        with ThreadPoolExecutor(max_workers=worker) as executor:
            future_l = [
                executor.submit(self._local_chat, i, query_l[i])
                for i in range(len(query_l))
            ]
            res_l = ["no response"] * len(query_l)
            for future in tqdm(as_completed(future_l), desc=desc, total=len(future_l)):
                i, resp = future.result()
                res_l[i] = resp
        return res_l
    
            
        