import logging
import time
from env.primitives import MarketEnvironment
from agents.llm_client import OllamaClient
from agents.skill_manager import SkillManager
from agents.action import ActionAgent
from agents.critic import CriticAgent
from agents.curriculum import CurriculumAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("QuantVoyager")

from duckduckgo_search import DDGS

class QuantVoyager:
    def __init__(self, model_name: str = "deepseek-r1:8b"):
        logger.info(f"Initializing QuantVoyager with model: {model_name}")
        self.env = MarketEnvironment()
        self.llm = OllamaClient(model=model_name)
        self.skills = SkillManager()
        
        self.action_agent = ActionAgent(self.llm, self.skills)
        self.critic_agent = CriticAgent(self.llm)
        self.curriculum_agent = CurriculumAgent(self.llm, self.skills)

    def perform_research(self, query: str):
        logger.info(f"Performing Web Research for: {query}")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3))
            
            context = "\n".join([f"Source: {r.get('title')}\nSnippet: {r.get('body')}" for r in results])
            
            prompt = f"Based on the following web search results, provide a concise summary of the trading concept or rule requested in the query '{query}'.\n\nResults:\n{context}"
            summary = self.llm.generate(prompt, system_prompt="You are a financial researcher. Summarize facts clearly.")
            
            # Extract a topic name
            topic_prompt = f"Give a 1-3 word topic name for this query: '{query}'. Output ONLY the topic name."
            topic_name = self.llm.generate(topic_prompt).strip()
            
            self.skills.add_knowledge(topic_name, summary)
            logger.info(f"Successfully learned new knowledge: {topic_name}")
        except Exception as e:
            logger.error(f"Research failed: {e}")

    def learn(self):
        """
        The main lifelong learning loop. Runs infinitely until manually stopped.
        """
        logger.info("Starting lifelong learning loop...")
        
        i = 0
        while True:
            i += 1
            try:
                logger.info(f"\n{'='*40}\nIteration {i} (Infinite Run)\n{'='*40}")
                
                # 1. Curriculum proposes a task
                state = self.env.get_portfolio_state()
                proposal = self.curriculum_agent.propose_next_task(state)
                
                task_type = proposal.get("type", "CODE")
                task_desc = proposal.get("task", "")
                
                if task_type == "RESEARCH":
                    self.perform_research(task_desc)
                    time.sleep(2)
                    continue
                    
                # 2. Action Agent tries to solve it (with max 3 retries)
                max_retries = 3
                success = False
                critique = ""
                best_code = ""
                
                for attempt in range(max_retries):
                    logger.info(f"Action Agent Attempt {attempt+1}/{max_retries}")
                    code = self.action_agent.generate_code(task_desc, critique)
                    
                    if code.startswith("LLM_ERROR"):
                        logger.error("LLM Server is down or unresponsive. Breaking attempt loop.")
                        break
                        
                    # 3. Environment executes the code
                    logger.info("Executing generated code in sandbox...")
                    execution_log = ""
                    try:
                        # Record portfolio before execution
                        state_before = self.env.get_portfolio_state()
                        
                        # We inject `env` into the local namespace for the exec
                        local_vars = {"env": self.env}
                        exec(code, globals(), local_vars)
                        
                        # Record portfolio after execution to calculate PnL
                        state_after = self.env.get_portfolio_state()
                        val_before = state_before["total_value"]
                        val_after = state_after["total_value"]
                        pnl = val_after - val_before
                        pnl_pct = (pnl / val_before * 100) if val_before > 0 else 0
                        
                        execution_log = f"Code executed successfully.\nStarting Portfolio Value: {val_before:.2f}\nEnding Portfolio Value: {val_after:.2f}\nTotal PnL: {pnl:.2f} ({pnl_pct:.2f}%)"
                    except Exception as e:
                        execution_log = f"Exception raised during execution: {type(e).__name__}: {str(e)}"
                        logger.warning(f"Execution failed: {e}")

                    # 4. Critic evaluates the execution
                    success, critique = self.critic_agent.evaluate(task_desc, code, execution_log)
                    
                    if success:
                        logger.info("Critic approved the execution! Task achieved.")
                        best_code = code
                        break
                    else:
                        logger.info(f"Critic rejected the execution. Critique: {critique}")
                        
                # 5. Skill Manager stores the successful skill
                if success and best_code:
                    # Ask LLM for a short descriptive name
                    name_prompt = f"Given this task: '{task_desc}', give a 2-3 word pythonic function name for it (e.g. 'buy_moving_average'). Output ONLY the name."
                    skill_name = self.llm.generate(name_prompt).strip().replace("'", "").replace('"', '')
                    
                    self.skills.add_skill(skill_name, best_code, task_desc)
                    logger.info(f"Skill '{skill_name}' added to the Vault.")
                else:
                    logger.warning(f"Failed to achieve task '{task_desc}' after {max_retries} attempts. Moving on to next curriculum goal.")
                    
                time.sleep(2) # Brief pause between iterations
                
            except Exception as e:
                logger.error(f"CRITICAL ERROR in main loop iteration {i}: {e}. Sleeping for 60 seconds before resuming...")
                import traceback
                traceback.print_exc()
                time.sleep(60)

if __name__ == "__main__":
    # Start the system using deepseek 8b as requested
    # Note: Assumes local ollama is running on port 11434 with 'deepseek-r1:8b' model pulled
    voyager = QuantVoyager(model_name="deepseek-r1:8b")
    voyager.learn()
