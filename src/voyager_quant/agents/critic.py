import logging
from typing import Tuple
from .llm_client import OllamaClient

logger = logging.getLogger(__name__)

class CriticAgent:
    """
    The Reviewer.
    Evaluates the execution of the Action Agent's code. Checks for logic flaws, 
    lookahead bias, and overall success against the Curriculum task.
    """
    def __init__(self, llm_client: OllamaClient):
        self.llm = llm_client
        self.system_prompt = """You are QuantVoyager Critic Agent, an expert quantitative risk manager.
Your job is to evaluate the execution results of a Python trading script.
You will be given the original task, the generated code, and the execution trace/logs (including any exceptions and PnL results).
CRITICAL: If the task specifies a PnL target (e.g. '+10% growth'), you MUST verify that the PnL in the execution log meets this target.
You must output your evaluation in two parts:
1. SUCCESS (Boolean): Output exactly 'SUCCESS: True' if the code ran without critical errors and achieved the task (including any PnL targets), otherwise 'SUCCESS: False'.
2. CRITIQUE (String): Provide a detailed critique. If failed, explain exactly what went wrong and how the Action Agent should fix it (e.g. syntax error, logic error, Guardian rejection, or PnL target missed)."""

    def evaluate(self, task: str, code: str, execution_log: str) -> Tuple[bool, str]:
        """
        Evaluates execution and returns (is_success, critique_string)
        """
        logger.info("CriticAgent: Evaluating execution...")
        prompt = f"""Task: {task}
        
Code:
```python
{code}
```

Execution Log:
{execution_log}

Evaluate the execution. Did it achieve the task? Were there any errors? 
Provide your response strictly in this format:
SUCCESS: <True/False>
CRITIQUE: <Your detailed feedback>
"""
        response = self.llm.generate(prompt, system_prompt=self.system_prompt)
        
        # Parse the response
        success = False
        critique = response
        
        lines = response.split('\n')
        for i, line in enumerate(lines):
            if line.upper().startswith("SUCCESS:"):
                success_val = line.split(":")[1].strip().lower()
                success = "true" in success_val
            elif line.upper().startswith("CRITIQUE:"):
                critique = "\n".join(lines[i:])
                break
                
        return success, critique
