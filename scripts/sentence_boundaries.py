"""Sentence-owned word edges. Silence is evidence, never sentence authority."""
import math
import re
from difflib import SequenceMatcher
from project_registry import digest

CONTRACT = 'sentence-owned-edges-v2-frame-estimate'
SELECTION_RULE = (
    '每个连续片段必须是完整原声句群。尤其每版第一段，必须查看原素材前文，'
    '确认不是上一句残尾、依赖回答或省略主语的承接句；字幕分行、标点和静音均不是独立句首证明。'
    '选择句首实际要保留的原文，不得让本地程序按附近较大停顿改选句子。'
    '无独立开头则换另一个完整句群，禁止拼词或把上句尾巴补到开头。'
)


def normalized(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()


def anchored_words(words, text, rough_start, rough_end):
    """Align the selected text, not the largest pause. Ambiguity fails closed.

    Match the full phrase to contextual ASR characters so repeated local words
    do not gain authority from a pause. Preserve entire ASR tokens at both ends.
    No audio timestamps are rewritten to disguise uncertain ASR alignment.
    """
    target = normalized(text)
    eligible = [(i, w) for i, w in enumerate(words)
                if w['end'] > rough_start - 2 and w['start'] < rough_end + 2]
    chars, owners = '', []
    for i, w in eligible:
        token = normalized(w['text'])
        chars += token
        owners.extend([i] * len(token))
    if len(target) < 4 or not chars:
        raise ValueError('sentence_alignment_insufficient_text')
    blocks = [b for b in SequenceMatcher(None, target, chars, autojunk=False).get_matching_blocks() if b.size]
    if not blocks or blocks[0].a != 0 or blocks[-1].a + blocks[-1].size != len(target):
        raise ValueError('sentence_edge_text_not_aligned_choose_another_sentence')
    first, last = owners[blocks[0].b], owners[blocks[-1].b + blocks[-1].size - 1]
    retained = normalized(''.join(w['text'] for w in words[first:last+1]))
    similarity = SequenceMatcher(None, target, retained, autojunk=False).ratio()
    if similarity < .72 or not retained.startswith(target[:min(3, len(target))]):
        raise ValueError('sentence_alignment_low_confidence')
    # Do not silently keep a token containing the previous sentence's suffix.
    if owners.index(first) != blocks[0].b or len(owners)-1-owners[::-1].index(last) != blocks[-1].b+blocks[-1].size-1:
        raise ValueError('sentence_onset_inside_asr_token')
    start, end = words[first]['start'], words[last]['end']
    if not rough_start - 2 <= start < end <= rough_end + 2:
        raise ValueError('sentence_alignment_outside_search_envelope')
    return first, last, similarity


def bounded_edges(words, first, last, fps, padding=.08):
    """Padding lives only in the gap between the selected and excluded words."""
    if not fps > 0 or not 0 <= first <= last < len(words):
        raise ValueError('sentence_edge_input_invalid')
    onset, offset = float(words[first]['start']), float(words[last]['end'])
    previous = float(words[first-1]['end']) if first else max(0., onset-padding)
    following = float(words[last+1]['start']) if last+1 < len(words) else offset+padding
    tolerance = .5/fps  # Frame rounding only; not a claim of phoneme accuracy.
    if previous > onset + 2*tolerance + 1e-9 or following < offset - 2*tolerance - 1e-9:
        raise ValueError('overlapping_asr_tokens_no_safe_sentence_edge')
    lo = math.floor(max(previous, onset-padding)*fps + 1e-8)
    hi = math.ceil(min(following, offset+padding)*fps - 1e-8)
    # ASR labels are estimates. A half-frame rounding discrepancy is not
    # proof that a syllable was cut, nor authority to reject the sentence.
    rounded = (lo/fps > onset + 1e-9 or hi/fps < offset - 1e-9
               or lo/fps < previous - 1e-9 or hi/fps > following + 1e-9)
    if rounded:
        min_lo = math.ceil((previous-tolerance)*fps - 1e-8)
        max_lo = math.floor((onset+tolerance)*fps + 1e-8)
        min_hi = math.ceil((offset-tolerance)*fps - 1e-8)
        max_hi = math.floor((following+tolerance)*fps + 1e-8)
        if min_lo > max_lo or min_hi > max_hi:
            raise ValueError('word_alignment_uncertain_needs_local_audio_review')
        lo = min(max(round(onset*fps), min_lo), max_lo)
        hi = min(max(round(offset*fps), min_hi), max_hi)
    if lo >= hi: raise ValueError('word_alignment_interval_too_short')
    body = {'contract': CONTRACT, 'first_word_start': onset, 'last_word_end': offset,
            'previous_word_end': previous, 'next_word_start': following,
            'source_start_frame': lo, 'source_end_frame': hi, 'fps': fps,
            'first_text': words[first]['text'], 'last_text': words[last]['text'],
            'first_word_index': first, 'last_word_index': last,
            'alignment_status': 'FRAME_ROUNDED_ESTIMATE' if rounded else 'WITHIN_ESTIMATED_WORD_GAP',
            'phoneme_accuracy_certified': False,
            'word_context_digest': digest(words)}
    return lo, hi, body


def verify_edges(clip):
    proof = clip.get('sentence_edges') or {}
    if proof.get('contract') != CONTRACT:
        return False
    try:
        fps = clip['fps']; lo = clip['source_start_frame']; hi = clip['source_end_frame']
        tolerance = .5/fps if proof.get('alignment_status') == 'FRAME_ROUNDED_ESTIMATE' else 0.
        words = clip['words']; first = proof['first_word_index']; last = proof['last_word_index']
        return (0 <= first <= last < len(words)
                and proof['first_word_start'] == words[first]['start']
                and proof['last_word_end'] == words[last]['end']
                and proof['word_context_digest'] == digest(clip['words'])
                and (lo, hi, fps) == (proof['source_start_frame'], proof['source_end_frame'], proof['fps'])
                and proof['previous_word_end']-tolerance-1e-9 <= lo/fps <= proof['first_word_start']+tolerance+1e-9
                and proof['last_word_end']-tolerance-1e-9 <= hi/fps <= proof['next_word_start']+tolerance+1e-9)
    except (KeyError, TypeError, ZeroDivisionError):
        return False
