"""Sentence-owned word edges. Silence is evidence, never sentence authority."""
import math
import re
import ctypes
from difflib import SequenceMatcher
from functools import lru_cache
from project_registry import digest

CONTRACT = 'sentence-owned-edges-v5-asr-optional-for-vision'
SELECTION_RULE = (
    '每个连续片段必须是完整原声句群。尤其每版第一段，必须查看原素材前文，'
    '确认不是上一句残尾、依赖回答或省略主语的承接句；字幕分行、标点和静音均不是独立句首证明。'
    '选择句首实际要保留的原文，不得让本地程序按附近较大停顿改选句子。'
    '无独立开头则换另一个完整句群，禁止拼词或把上句尾巴补到开头。'
)


def normalized(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()


@lru_cache(maxsize=4096)
def phonetic(text):
    """Local macOS transliteration for nearby homophones in uncalibrated captions."""
    try:
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateMutableCopy.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFStringCreateMutableCopy.restype = ctypes.c_void_p
        cf.CFStringTransform.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        cf.CFStringTransform.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        encoding = 0x08000100  # UTF-8
        source = cf.CFStringCreateWithCString(None, text.encode('utf-8'), encoding)
        transform = cf.CFStringCreateWithCString(None, b'Any-Latin; Latin-ASCII', encoding)
        mutable = cf.CFStringCreateMutableCopy(None, 0, source)
        try:
            if not cf.CFStringTransform(mutable, None, transform, False):
                return ''
            output = ctypes.create_string_buffer(max(256, len(text) * 32))
            if not cf.CFStringGetCString(mutable, output, len(output), encoding):
                return ''
            return normalized(output.value.decode('utf-8'))
        finally:
            for value in (source, transform, mutable):
                if value:
                    cf.CFRelease(value)
    except (OSError, UnicodeError, ValueError):
        return ''


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


def locate_subtitle_edge(words, text, cue_time, *, ending=False, search_seconds=6.0):
    """Find a subtitle phrase in nearby ASR words; subtitle time is a search hint.

    ASR may substitute or omit a character. Match complete token spans and
    reject competing occurrences or a match that begins/ends inside a token.
    The returned token edge is still an estimate for the later audio review.
    """
    target = normalized(text)
    try:
        return _locate_subtitle_edge_once(words, target, cue_time, ending=ending,
                                          search_seconds=search_seconds)
    except ValueError as exc:
        if str(exc) != 'sentence_local_text_not_found_expand_asr_window' or len(target) <= 8:
            raise
        # A longer subtitle cue can contain an ASR omission away from the
        # physical edge. Keep the nearest characters as a bounded fallback.
        near_edge = target[-8:] if ending else target[:8]
        return _locate_subtitle_edge_once(words, near_edge, cue_time,
                                          ending=ending, search_seconds=search_seconds)


def _locate_subtitle_edge_once(words, target, cue_time, *, ending, search_seconds):
    if len(target) < 4 or not words:
        raise ValueError('sentence_local_text_insufficient')
    maximum_difference = max(2, math.ceil(len(target) * .25))
    minimum = max(2, len(target) - maximum_difference)
    maximum = len(target) + maximum_difference
    candidates = []
    for first, word in enumerate(words):
        if (float(word['end']) < cue_time - search_seconds - 3
                or float(word['start']) > cue_time + search_seconds + 3):
            continue
        phrase = ''
        for last in range(first, len(words)):
            phrase += normalized(str(words[last]['text']))
            if len(phrase) > maximum:
                break
            if len(phrase) < minimum:
                continue
            edge_time = float(words[last]['end'] if ending else words[first]['start'])
            if abs(edge_time - cue_time) > search_seconds:
                continue
            matcher = SequenceMatcher(None, target, phrase, autojunk=False)
            ratio = matcher.ratio()
            blocks = [block for block in matcher.get_matching_blocks() if block.size]
            coverage = sum(block.size for block in blocks) / len(target) if blocks else 0.0
            lexical = (ratio >= (.78 if len(target) >= 8 else .80 if len(target) >= 6 else .85)
                       and coverage >= .75)
            if lexical:
                extra_before = blocks[0].b
                extra_after = len(phrase) - (blocks[-1].b + blocks[-1].size)
                # An uncalibrated caption may replace the first character of
                # a two-character spoken token (这是/就是). The lexical matcher
                # then starts at character two, although the whole ASR token
                # belongs to this sentence. Accept only a one-character
                # substitution with a strongly matching continuation, and
                # retain the entire first token at its original onset.
                first_token = normalized(str(words[first]['text']))
                if (not ending and extra_before == 1 and extra_after == 0
                        and len(target) >= 10 and len(phrase) >= 10
                        and len(first_token) == 2 and phrase.startswith(first_token)
                        and target[0] != phrase[0] and target[1] == phrase[1]
                        and float(words[first]['start']) <= cue_time <= float(words[first]['end']) + .1
                        and SequenceMatcher(None, target[1:], phrase[1:],
                                            autojunk=False).ratio() >= .9):
                    extra_before = 0
            else:
                # Similar pronunciation is sufficient only in this narrow ASR
                # neighborhood. Keep the first and last syllables at word edges.
                if len(target) < 6 or not re.fullmatch(r'[\u4e00-\u9fff]+', target):
                    continue
                target_sound, phrase_sound = phonetic(target), phonetic(phrase)
                if not target_sound or not phrase_sound:
                    continue
                sound_ratio = SequenceMatcher(None, target_sound, phrase_sound,
                                              autojunk=False).ratio()
                same_onset = phonetic(target[0]) == phonetic(phrase[0])
                # 剪映 may render a spoken connector differently (e.g. 然后/而).
                # Permit this only when the following phrase aligns strongly.
                connective_shift = (not ending and len(target) >= 10 and len(phrase) >= 9
                    and phonetic(target[2:5]) == phonetic(phrase[1:4])
                    and SequenceMatcher(None, phonetic(target[2:]), phonetic(phrase[1:]),
                                        autojunk=False).ratio() >= .90)
                minimum_sound = .83 if connective_shift else .86
                if (sound_ratio < minimum_sound or not (same_onset or connective_shift)
                        or (ending and phonetic(target[-1]) != phonetic(phrase[-1]))):
                    continue
                ratio, coverage = sound_ratio, sound_ratio
                extra_before = extra_after = 0
            candidates.append((ratio, coverage, abs(edge_time - cue_time), first, last,
                               extra_before, extra_after, abs(len(target) - len(phrase))))
    if not candidates:
        raise ValueError('sentence_local_text_not_found_expand_asr_window')
    candidates.sort(key=lambda row: (-row[0], -row[1], row[7], row[2]))
    best = candidates[0]
    for other in candidates[1:]:
        if (other[4] < best[3] or other[3] > best[4]) and other[0] >= best[0] - .06:
            raise ValueError('sentence_local_text_ambiguous')
    if (not ending and best[5] > 0) or (ending and best[6] > 0):
        raise ValueError('sentence_edge_inside_asr_token_requires_local_audio')
    return best[3], best[4], best[0]


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
