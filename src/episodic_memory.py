"""
episodic_memory.py - Cross-task learning via stored tool chains

Stores successful tool call sequences and retrieves them for similar queries.
Uses simple keyword matching (consistent with ifs_knowledge.yaml approach).

Usage:
    memory = EpisodicMemory("./cache/memory")
    memory.store(query, tool_chain, result_summary)
    relevant = memory.retrieve(query, top_k=3)
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional


class EpisodicMemory:
    """Store and retrieve successful tool chains for cross-task learning."""

    def __init__(
        self,
        cache_dir: str = "./cache/memory",
        max_memories: int = 100,
        retrieval_top_k: int = 5,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.cache_dir / "episodic_memories.json"
        self.max_memories = max_memories
        self.retrieval_top_k = retrieval_top_k
        self._memories: list = []
        self._load()

    def _load(self):
        """Load memories from disk."""
        if self.memory_file.exists():
            try:
                with open(self.memory_file) as f:
                    self._memories = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._memories = []

    def _save(self):
        """Persist memories to disk."""
        try:
            with open(self.memory_file, "w") as f:
                json.dump(self._memories, f, indent=2)
        except IOError:
            pass  # Best effort persistence

    def _extract_keywords(self, text: str) -> set:
        """Extract keywords from text for matching."""
        # Lowercase and split on non-alphanumeric
        words = re.split(r"[^a-z0-9]+", text.lower())
        # Filter short words and common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                      "to", "for", "and", "or", "in", "on", "at", "of", "with"}
        return {w for w in words if len(w) > 2 and w not in stop_words}

    def _compute_similarity(self, keywords1: set, keywords2: set) -> float:
        """Compute Jaccard similarity between two keyword sets."""
        if not keywords1 or not keywords2:
            return 0.0
        return len(keywords1 & keywords2) / len(keywords1 | keywords2)

    def store(
        self,
        query: str,
        tool_chain: list,
        result_summary: str,
        success: bool = True,
    ):
        """Store a successful tool chain for future retrieval.

        EFFICIENCY OPTIMIZATION: If a similar query exists with a LONGER tool chain,
        replace it with this shorter/more efficient chain. This ensures we learn
        the best approach over time.

        Args:
            query: The user query that triggered this chain
            tool_chain: List of tool calls [{"name": "...", "args": {...}}]
            result_summary: Brief summary of the outcome
            success: Whether the chain was successful
        """
        if not success or not tool_chain:
            return  # Only store successful chains

        query_keywords = self._extract_keywords(query)
        chain_length = len(tool_chain)

        # Check for similar existing memories
        # If we find one with >70% keyword overlap and a LONGER chain, replace it
        replaced = False
        for i, existing in enumerate(self._memories):
            existing_keywords = set(existing.get("keywords", []))
            similarity = self._compute_similarity(query_keywords, existing_keywords)

            if similarity > 0.7:  # Similar enough to be the "same" query
                existing_chain_length = len(existing.get("tool_chain", []))

                if chain_length < existing_chain_length:
                    # New chain is more efficient - replace!
                    self._memories[i] = {
                        "query": query,
                        "keywords": list(query_keywords),
                        "tool_chain": tool_chain,
                        "chain_length": chain_length,
                        "result_summary": result_summary,
                        "timestamp": datetime.now().isoformat(),
                        "replaced_longer_chain": existing_chain_length,  # Track improvement
                    }
                    replaced = True
                    print(f"[EpisodicMemory] Replaced {existing_chain_length}-call chain with {chain_length}-call chain")
                    break
                elif chain_length >= existing_chain_length:
                    # Existing chain is same length or shorter - don't add duplicate
                    return

        if not replaced:
            memory = {
                "query": query,
                "keywords": list(query_keywords),
                "tool_chain": tool_chain,
                "chain_length": chain_length,
                "result_summary": result_summary,
                "timestamp": datetime.now().isoformat(),
            }
            self._memories.append(memory)

        # Prune oldest if over limit
        if len(self._memories) > self.max_memories:
            self._memories = self._memories[-self.max_memories:]

        self._save()

    def retrieve(self, query: str, top_k: Optional[int] = None) -> list:
        """Retrieve relevant past experiences for a query.

        EFFICIENCY BONUS: Shorter tool chains get a score boost to prefer
        efficient solutions over verbose ones.

        Args:
            query: The current user query
            top_k: Number of memories to retrieve (default: retrieval_top_k)

        Returns:
            List of relevant memories sorted by relevance and efficiency
        """
        if not self._memories:
            return []

        top_k = top_k or self.retrieval_top_k
        query_keywords = self._extract_keywords(query)

        if not query_keywords:
            return []

        # Score memories by keyword overlap + efficiency bonus
        scored = []
        for mem in self._memories:
            mem_keywords = set(mem.get("keywords", []))
            overlap = len(query_keywords & mem_keywords)
            if overlap > 0:
                # Base score: keyword overlap (Jaccard similarity)
                base_score = overlap / len(query_keywords | mem_keywords)

                # Efficiency bonus: shorter chains score higher
                # 1 tool call = 0.3 bonus, 5 calls = 0.1 bonus, 20+ calls = 0 bonus
                chain_length = len(mem.get("tool_chain", []))
                efficiency_bonus = max(0, 0.3 - (chain_length - 1) * 0.01)

                final_score = base_score + efficiency_bonus
                scored.append((final_score, mem))

        # Sort by score descending (relevance + efficiency)
        scored.sort(key=lambda x: x[0], reverse=True)

        return [mem for _, mem in scored[:top_k]]

    def format_for_prompt(self, memories: list) -> str:
        """Format retrieved memories for injection into system prompt.

        IMPORTANT: Highlights efficient approaches and explains WHY they worked.

        Args:
            memories: List of memory dicts from retrieve()

        Returns:
            Formatted string for system prompt
        """
        if not memories:
            return ""

        lines = ["## Past Successful Approaches (prefer shorter tool chains)"]
        for i, mem in enumerate(memories, 1):
            tool_chain = mem.get("tool_chain", [])
            chain_length = len(tool_chain)
            tools = " → ".join(t.get("name", "?") for t in tool_chain[:5])  # Show first 5
            if chain_length > 5:
                tools += f" → ... ({chain_length} total)"

            # Highlight efficiency
            efficiency = "⭐ EFFICIENT" if chain_length <= 3 else ("✓ Good" if chain_length <= 10 else "")

            query_preview = mem.get('query', '')[:100]
            if len(mem.get('query', '')) > 100:
                query_preview += "..."

            lines.append(f"\n**Example {i}:** \"{query_preview}\" {efficiency}")
            lines.append(f"- Tool chain ({chain_length} calls): {tools}")

            # For efficient chains, show the key tool with args as a pattern to follow
            if chain_length <= 3 and tool_chain:
                key_tool = tool_chain[0]
                args_preview = str(key_tool.get("args", {}))[:150]
                lines.append(f"- **KEY**: `{key_tool.get('name')}({args_preview})`")

        lines.append("\n**INSTRUCTION**: Prefer approaches with fewer tool calls. The examples above show proven patterns.")

        return "\n".join(lines)

    def deduplicate(self) -> int:
        """Remove duplicate/inefficient memories for similar queries.

        Returns:
            Number of memories removed
        """
        if len(self._memories) < 2:
            return 0

        # Group by keyword similarity
        to_remove = set()
        for i, mem_i in enumerate(self._memories):
            if i in to_remove:
                continue
            keywords_i = set(mem_i.get("keywords", []))
            chain_len_i = len(mem_i.get("tool_chain", []))

            for j, mem_j in enumerate(self._memories[i + 1:], start=i + 1):
                if j in to_remove:
                    continue
                keywords_j = set(mem_j.get("keywords", []))
                similarity = self._compute_similarity(keywords_i, keywords_j)

                if similarity > 0.7:
                    # Similar queries - keep the shorter chain
                    chain_len_j = len(mem_j.get("tool_chain", []))
                    if chain_len_j > chain_len_i:
                        to_remove.add(j)
                    elif chain_len_i > chain_len_j:
                        to_remove.add(i)
                        break  # i is removed, stop comparing

        # Remove marked memories
        removed_count = len(to_remove)
        self._memories = [m for idx, m in enumerate(self._memories) if idx not in to_remove]

        if removed_count > 0:
            self._save()
            print(f"[EpisodicMemory] Deduplicated: removed {removed_count} inefficient memories")

        return removed_count

    def clear(self):
        """Clear all memories."""
        self._memories = []
        self._save()


# Singleton instance
_instance: Optional[EpisodicMemory] = None


def get_episodic_memory(
    cache_dir: str = "./cache/memory",
    max_memories: int = 100,
    retrieval_top_k: int = 5,
) -> EpisodicMemory:
    """Get or create the singleton episodic memory instance."""
    global _instance
    if _instance is None:
        _instance = EpisodicMemory(
            cache_dir=cache_dir,
            max_memories=max_memories,
            retrieval_top_k=retrieval_top_k,
        )
    return _instance
