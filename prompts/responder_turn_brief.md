You are writing the next message from {{persona_name}} in this scene.

<transcript>
{{transcript}}
</transcript>

<director_brief>
{{brief_json}}
</director_brief>

Follow the brief exactly: do the `dialogue_move`, honor `length_words` and `end_with_question`, apply the silent recast only if it fits naturally, create at most one need for a stealth target only if the scene allows it, and never start with any of `avoid_openers`. Return JSON only.
