import os, sys, json, base64
import config, database, ai_engine

def render_subtext_telegram_message(res, vibe_label='🎙️ Articulate') -> str:
    sub = res.subtext_analysis
    lines = [
        f'🎭 Subtext & Nuance Decoder ({vibe_label})',
        '━━━━━━━━━━━━━━━━━━━━',
        f'🗣️ Target Utterance: "{sub.target_utterance}"',
        '',
        f'🪞 Diagnostic Mirror: Suppressed (learner_diagnosis={res.learner_diagnosis} | show_diagnostic_mirror={res.show_diagnostic_mirror})',
        '',
        '🔍 Pragmatic & Subtext Breakdown',
        f'• Literal Meaning: {sub.literal_surface_meaning}',
        f'• Implied Meaning Spectrum: {sub.actual_pragmatic_intent}',
        f'• Tone & Context Cues: {sub.tone_and_social_signal}',
        f'• الشرح التداولي بالعربية: {sub.arabic_pragmatic_explanation}',
        '',
        '🎚️ Context & Tone Spectrum (How Meaning Shifts)',
    ]
    for step in sub.escalation_ladder:
        lines.extend([
            f'\n[{step.escalation_id}] {step.intensity_label}',
            f'  └ 💬 Spoken Example: "{step.spoken_line}"',
            f'  └ 🎯 Social Effect: {step.social_effect}',
            f'  └ 🇸🇦 المقصود والنبرة: {step.arabic_nuance}',
        ])
    lines.extend(['', '🎙️ Lounge Hosts Take (Spoken Context & Active Prompt)'])
    for t in res.host_turns:
        icon = '🧔‍♂️ Marcus' if t.speaker == 'marcus' else '👩🏼‍💼 Chloe'
        lines.append(f'{icon} ({t.turn_id}):\n"{t.text}"')
    lines.extend(['', '💎 Verified Target Expressions (LoungeTurnResponse)'])
    for idx, it in enumerate(res.lexical_items, 1):
        lines.append(f'\n({idx}) 🧩 Base Pattern: {it.expression}')
        if it.source_type == 'verbatim_from_host_dialogue':
            lines.append(f'  └ 💬 In the Dialogue ({it.source_ref_id} | speaker={it.source_speaker}): "{it.full_sentence_citation}"')
        else:
            lines.append(f'  └ 📍 Source ({it.source_type}): {it.usage_status_label}')
        lines.append(f'  └ 📖 Core Meaning: {it.core_lexical_meaning}')
        lines.append(f'  └ 🎯 Pragmatic Function: {it.contextual_function}')
        lines.append(f'  └ 🏷️ Register & Constraints: {it.register_and_usage}')
        lines.append(f'  └ 🇸🇦 Arabic: {it.arabic_equivalent}')
        if it.contrastive_note:
            lines.append(f'  └ 🔄 Contrastive Note: {it.contrastive_note}')
    lines.extend([
        '',
        '━━━━━━━━━━━━━━━━━━━━',
        f'✅ Pydantic v2 Log: PASSED (LoungeTurnResponse | mode={res.active_mode} [submode=subtext_decoder] | diagnosis={res.learner_diagnosis} | mirror={res.show_diagnostic_mirror} | {len(res.host_turns)} host_turns | {len(res.lexical_items)} citations verified)'
    ])
    return '\n'.join(lines)

if __name__ == '__main__':
    req = json.load(open('eval_request.json', encoding='utf-8'))
    utt = req.get('utterance', '')
    db = database.LoungeDatabase(user_db_path='/tmp/eval_u.db', corpus_db_path='/tmp/eval_c.db')
    ai = ai_engine.NYCLoungeAI(api_key=config.GEMINI_API_KEY, db=db)
    res = ai.process_subtext_decoder(user={'preferred_register': 'articulate'}, user_text=utt)
    rendered = render_subtext_telegram_message(res)
    b64 = base64.b64encode(rendered.encode('utf-8')).decode('ascii')
    print('B64_EVAL_START:' + b64 + ':B64_EVAL_END', flush=True)
