"""Conservative routing for explicit requests about the recorded business ledger."""

import re


def is_recorded_fact_request(question: str, memory_mode: str = "balanced") -> bool:
    if memory_mode == "off" or question.lstrip().startswith("/"):
        return False
    if re.search(
        r"\b(?:why|explain|quote|verbatim|transcript|document|source|compare)\b|"
        r"为什么|为何|解释|原文|依据|材料|比较|对比",
        question,
        re.I,
    ):
        return False
    return bool(
        re.search(r"\b(?:recorded|registered|stored)\b|已记录|已登记|已保存", question, re.I)
        and re.search(
            r"\b(?:tasks?|decisions?|action items?|project status|project owner)\b|"
            r"任务|待办|决策|决定|项目状态|项目负责人",
            question,
            re.I,
        )
    )
