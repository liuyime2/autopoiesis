import json
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SkillManager:
    """
    The Vault: Stores executable Python skills and conceptual knowledge learned from the web.
    """
    def __init__(self, storage_dir: str = "./skill_library"):
        self.storage_dir = storage_dir
        self.skills_file = os.path.join(storage_dir, "skills.json")
        self.knowledge_file = os.path.join(storage_dir, "knowledge.json")
        
        os.makedirs(self.storage_dir, exist_ok=True)
        
        self.skills = self._load(self.skills_file)
        self.knowledge = self._load(self.knowledge_file)

    def _load(self, filepath: str) -> Dict[str, Any]:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
        return {}

    def _save(self, filepath: str, data: Dict[str, Any]):
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)

    def add_skill(self, skill_name: str, code: str, description: str):
        """
        Store an executable trading function or strategy.
        """
        logger.info(f"SkillManager: Adding new skill '{skill_name}'")
        self.skills[skill_name] = {
            "code": code,
            "description": description
        }
        self._save(self.skills_file, self.skills)

    def add_knowledge(self, topic: str, content: str):
        """
        Store conceptual market knowledge (e.g. from web searches).
        """
        logger.info(f"SkillManager: Adding new knowledge on '{topic}'")
        self.knowledge[topic] = {
            "content": content
        }
        self._save(self.knowledge_file, self.knowledge)

    def retrieve_skills(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieve relevant skills. 
        TODO: Implement proper vector embedding search (e.g. ChromaDB).
        Currently using naive substring matching for the MVP.
        """
        results = []
        for name, data in self.skills.items():
            if query.lower() in name.lower() or query.lower() in data['description'].lower():
                results.append({"name": name, **data})
        return results[:top_k]

    def retrieve_knowledge(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieve relevant market rules/knowledge.
        TODO: Implement proper vector embedding search.
        """
        results = []
        for topic, data in self.knowledge.items():
            if query.lower() in topic.lower() or query.lower() in data['content'].lower():
                results.append({"topic": topic, **data})
        return results[:top_k]
