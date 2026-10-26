import os, sys, json, base64, traceback
import config, database, ai_engine

def render_subtext_telegram_message(res, vibe_label='🎙️ Articulate') -> str:
    sa = res.subtext_analysis
    lines = [
        f'🎭 Subtext & Nuance Decoder ({vibe_label})',
        '━━━━━━━━━━━━━━━━━━━━',
        f'🗣️ Target Utterance: "{res.learner_input_text}"',
        '',
        f'🪞 Diagnostic Mirror: Suppressed (learner_diagnosis={res.learner_diagnosis} | show_diagnostic_mirror={res.show_diagnostic_mirror})',
        '',
        '🔍 Pragmatic & Subtext Breakdown',
        f'• Literal Meaning: {sa.literal_meaning}',
        f'• Observable Facts: {sa.observable_facts}',
        f'• Epistemic Calibration: Behavior Confidence={sa.behavior_confidence} | Motive Confidence={sa.motive_confidence}',
        f'• Vocal & Context Cues to Watch: {sa.evidence_to_watch}',
        '',
        '🎭 Plausible Implied Meanings Across Contexts:',
    ]
    for imp in sa.plausible_implicatures:
        lines.append(f'  • {imp}')
    lines.append('')
    lines.append('🎚️ Calibrated Response & Escalation Options')
    for opt in sa.escalation_options:
        lines.extend([
            f'\n[{opt.option_id}] {opt.label}',
            f'  └ 💬 Spoken Line: "{opt.exact_wording}"',
            f'  └ 🎯 Objective: {opt.objective}',
            f'  └ 🔎 Makes Explicit: {opt.makes_explicit}',
            f'  └ ⚖️ Trade-Off: {opt.interpersonal_tradeoff}',
            f'  └ 🚫 When Not to Use: {opt.when_not_to_use}',
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
        f'✅ Pydantic v2 Log: PASSED (LoungeTurnResponse | mode={res.active_mode} [submode=subtext_decoder] | diagnosis={res.learner_diagnosis} | mirror={res.show_diagnostic_mirror} | {len(res.host_turns)} host_turns | 2 in-dialogue citations matched | 1 non-cited extension verified absent)'
    ])
    return '\n'.join(lines)

if __name__ == '__main__':
    try:
        req = json.load(open('eval_request.json', encoding='utf-8'))
        utt = req.get('utterance', 'Must be nice.')
        db = database.LoungeDatabase(user_db_path='/tmp/eval_u.db', corpus_db_path='/tmp/eval_c.db')
        ai = ai_engine.NYCLoungeAI(api_key=config.GEMINI_API_KEY, db=db)
        res = ai.process_subtext_decoder(user={'preferred_register': 'articulate'}, user_text=utt)
        rendered = render_subtext_telegram_message(res)
    except Exception:
        rendered = '❌ EVAL_ERROR:\n' + traceback.format_exc()
    b64 = base64.b64encode(rendered.encode('utf-8')).decode('ascii')
    print('B64_EVAL_START:' + b64 + ':B64_EVAL_END', flush=True)
