import logging
import re
from typing import Dict, Any, Tuple
from .llm_client import OllamaClient
from .skill_manager import SkillManager

logger = logging.getLogger(__name__)

class ActionAgent:
    """
    The Coder / Executor.
    Writes Python code to achieve the curriculum task using available skills and primitives.
    """
    def __init__(self, llm_client: OllamaClient, skill_manager: SkillManager):
        self.llm = llm_client
        self.skills = skill_manager
        
        self.system_prompt = """You are QuantVoyager Action Agent, an expert Python quantitative developer.
Your job is to write executable Python code to solve a given trading task.
You have access to the following control primitives in the 'env' object:
- env.get_historical_data(symbol, timeframe, limit)
- env.get_current_price(symbol)
- env.get_portfolio_state()
- env.submit_order(symbol, quantity, side, order_type, limit_price)

Write ONLY valid Python code inside ```python ``` blocks.
Do not invent APIs. Use ONLY the provided env primitives and standard Python libraries (pandas, numpy, math).
CRITICAL: You must define any variables you use (like 'symbol = "AAPL"'). Do not assume they are provided.
CRITICAL: Do NOT use `return` outside of a function definition. Your code is executed as a script.
Your code will be executed in a restricted sandbox."""

    def generate_code(self, task: str, critique: str = "") -> str:
        """
        Generates Python code for the task. Uses previous critique if retrying.
        """
        # Retrieve relevant skills and knowledge for context
        relevant_skills = self.skills.retrieve_skills(task, top_k=2)
        relevant_knowledge = self.skills.retrieve_knowledge(task, top_k=2)
        
        context = "### Relevant Past Skills:\n"
        for s in relevant_skills:
            context += f"Skill '{s['name']}':\n```python\n{s['code']}\n```\n\n"
            
        context += "### Relevant Market Knowledge:\n"
        for k in relevant_knowledge:
            context += f"Topic '{k['topic']}': {k['content']}\n\n"

        prompt = f"Task: {task}\n\n{context}\n"
        if critique:
            prompt += f"### Previous Failure Critique:\n{critique}\n\nPlease fix the code based on the critique.\n"
            
        prompt += "\nOutput your solution in a single ```python\n<CODE>\n``` block."

        logger.info(f"ActionAgent: Requesting code for task '{task}'...")
        response = self.llm.generate(prompt, system_prompt=self.system_prompt)
        
        # Extract code from markdown block
        return self._extract_code(response)

    def _extract_code(self, text: str) -> str:
        # Remove <think> blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        
        # Match ```python or just ```
        match = re.search(r"```(?:python)?\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Fallback if no markdown block
        return text.strip()
