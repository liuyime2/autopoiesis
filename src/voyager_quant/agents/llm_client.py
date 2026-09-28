import requests
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class OllamaClient:
    """
    Client for communicating with local Ollama instance.
    Uses 'deepseek-r1:8b' or similar deepseek model as specified by the user.
    """
    def __init__(self, model: str = "deepseek-r1:8b", base_url: Optional[str] = None):
        # The user requested 'deepseek 8b'. Depending on the exact Ollama tag, it could be 'deepseek-coder', 'deepseek-llm', etc.
        # We will default to deepseek-r1:8b.
        self.model = model 
        if base_url is None:
            import os
            host = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
            if not host.startswith("http"):
                base_url = f"http://{host}"
            else:
                base_url = host
        self.base_url = base_url

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """
        Standard generation endpoint for Ollama.
        """
        url = f"{self.base_url}/api/generate"
        
        full_prompt = prompt
        if system_prompt:
             full_prompt = f"System: {system_prompt}\n\nUser: {prompt}"

        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "temperature": 0.2
        }

        for attempt in range(3):
            try:
                # Increase timeout to 1800s (30 mins) to allow deepseek-r1 to finish its <think> chain
                response = requests.post(url, json=payload, timeout=1800)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
            except requests.exceptions.RequestException as e:
                logger.error(f"Ollama generation failed (attempt {attempt+1}): {e}")
                import time
                time.sleep(5)
        
        # If all retries fail, return an explicit error tag so agents know it failed structurally
        logger.error(f"Failed to communicate with Ollama after 3 attempts.")
        return "LLM_ERROR: Cannot connect to Ollama server."
