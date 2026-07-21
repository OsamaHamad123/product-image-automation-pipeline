# verification_layer/use_cases/grpo_query_reformulator.py
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple


@dataclass
class GRPOQueryReformulationResult:
    original_query: str
    thought_process: str
    reformulated_query: str
    grpo_advantage_scores: List[float]
    best_candidate_idx: int
    format_reward_passed: bool


class GRPOQueryReformulator:
    """
    محرك التكيف الذاتي للاستعلامات المبني على التغذية الراجعة (GRPO RL-based CoT Query Reformulator)
    - Group Relative Policy Optimization (GRPO) advantage calculation:
      A_i = (R_i - mean(R)) / (std(R) + 1e-8)
    - Chain-of-Thought formatting: <think> ... </think> and <query> ... </query>
    """

    @classmethod
    def calculate_grpo_advantages(cls, rewards: List[float]) -> List[float]:
        if not rewards:
            return []
        arr = np.array(rewards, dtype=np.float32)
        mean_r = np.mean(arr)
        std_r = np.std(arr)
        advantages = (arr - mean_r) / (std_r + 1e-8)
        return [float(round(a, 4)) for a in advantages]

    @classmethod
    def reformulate_query_cot(
        cls, original_query: str, sample_candidate_responses: List[Tuple[str, float]]
    ) -> GRPOQueryReformulationResult:
        """
        sample_candidate_responses: [(response_text, reward_score), ...]
        """
        rewards = [score for _, score in sample_candidate_responses]
        advantages = cls.calculate_grpo_advantages(rewards)

        best_idx = int(np.argmax(rewards)) if rewards else 0
        best_resp, _ = sample_candidate_responses[best_idx] if sample_candidate_responses else ("", 0.0)

        # Parse <think> and <query>
        thought = "Analyzing product terminology, synonyms, and brand context."
        query = original_query

        if "<think>" in best_resp and "</think>" in best_resp:
            thought = best_resp.split("<think>")[1].split("</think>")[0].strip()

        if "<query>" in best_resp and "</query>" in best_resp:
            query = best_resp.split("<query>")[1].split("</query>")[0].strip()

        format_passed = "<think>" in best_resp and "<query>" in best_resp

        return GRPOQueryReformulationResult(
            original_query=original_query,
            thought_process=thought,
            reformulated_query=query,
            grpo_advantage_scores=advantages,
            best_candidate_idx=best_idx,
            format_reward_passed=format_passed,
        )
