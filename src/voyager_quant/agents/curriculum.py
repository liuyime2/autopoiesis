import logging
from typing import List, Dict, Any
from .llm_client import OllamaClient
from .skill_manager import SkillManager

logger = logging.getLogger(__name__)

class CurriculumAgent:
    """
    The Director.
    Decides what trading concept or strategy to explore next, based on current 
    portfolio state and previously learned skills.
    """
    def __init__(self, llm_client: OllamaClient, skill_manager: SkillManager):
        self.llm = llm_client
        self.skills = skill_manager
        
        self.system_prompt = """You are QuantVoyager Curriculum Agent, a Head of Quantitative Research.
Your job is to propose the next exploration task for your team to learn trading rules and build skills.
You have two types of tasks you can propose:
1. RESEARCH: To search the web and learn about a market rule, risk concept, or trading indicator (e.g., "What is the PDT rule?", "How does RSI work?").
2. CODE: To write a Python script that executes a strategy based on known rules.

You must output your proposal in exactly this format:
TYPE: <RESEARCH or CODE>
TASK: <The specific research question or coding task>

If the agent lacks basic knowledge (like market hours, risk limits, common indicators), propose RESEARCH first."""

    def propose_next_task(self, portfolio_state: Dict[str, Any]) -> Dict[str, str]:
        """
        Suggests the next logical objective as a dict with 'type' and 'task'.
        """
        logger.info("CurriculumAgent: Thinking about the next task...")
        
        known_skills = list(self.skills.skills.keys())
        known_topics = list(self.skills.knowledge.keys())
        
        # Calculate daily growth
        total_value = float(portfolio_state.get('total_value', 100000))
        initial_value = float(portfolio_state.get('initial_value', 100000))
        
        if initial_value > 0:
            growth_pct = ((total_value - initial_value) / initial_value) * 100.0
        else:
            growth_pct = 0.0
            
        logger.info(f"Current growth: {growth_pct:.2f}% (Target: +10%)")
        
        # Enforce evolution: Driven by the 10% daily growth target
        if growth_pct < 10.0:
            type_instruction = f"URGENT GROWTH TARGET: We are currently at {growth_pct:.2f}% growth. Our daily goal is +10.0%. You MUST propose a CODE task to execute an aggressive but calculated quantitative trade strategy to reach this target! Do NOT research!"
        elif len(known_topics) >= 5:
            type_instruction = "We have achieved our daily growth target and have plenty of market knowledge. Propose a CODE task to build more advanced quantitative strategies."
        else:
            type_instruction = "We have achieved our daily growth target! Propose a RESEARCH task to learn more trading rules, OR a CODE task to test a theory."

        prompt = f"""Current Portfolio Value: ${total_value} (Initial: ${initial_value})
Known Trading Skills: {known_skills}
Known Market Concepts: {known_topics}

{type_instruction}
Propose EXACTLY ONE new task.
Format:
TYPE: <RESEARCH/CODE>
TASK: <description>"""

        response = self.llm.generate(prompt, system_prompt=self.system_prompt)
        
        task_type = "CODE"
        task_desc = response.strip()
        
        for line in response.split('\n'):
            if line.startswith("TYPE:"):
                task_type = line.split(":", 1)[1].strip().upper()
            elif line.startswith("TASK:"):
                task_desc = line.split(":", 1)[1].strip()
                
        # Hard fallback for growth
        if growth_pct < 10.0 and task_type != "CODE":
            task_type = "CODE"
            
        logger.info(f"CurriculumAgent proposed [{task_type}]: {task_desc}")
        return {"type": task_type, "task": task_desc}
