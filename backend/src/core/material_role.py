"""Shared deterministic material-role inference for ingestion and retrieval."""


def infer_material_role(file_name: str, file_type: str) -> str:
    folded = file_name.casefold()
    # Container format is not a business role: a lecture recording is reference
    # knowledge, not an authoritative meeting transcript. Unknown media stays
    # conservative until the uploader explicitly chooses a meeting role.
    if any(
        marker in folded
        for marker in ("lecture", "course", "research_talk", "课程", "讲座", "公开课")
    ):
        return "attachment"
    if any(
        marker in folded for marker in ("meeting", "transcript", "会议记录", "会议录音", "会议录像")
    ):
        return "transcript"
    if any(marker in folded for marker in ("minutes", "纪要")):
        return "minutes"
    if any(marker in folded for marker in ("agenda", "议程")):
        return "agenda"
    if any(marker in folded for marker in ("decision", "决议", "决定")):
        return "decision_log"
    return "attachment"
