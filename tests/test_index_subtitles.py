"""Synthetic inputs only, including process concurrency and publication faults."""
import json
import multiprocessing
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_index_titles as title_fixtures
import index_subtitles as subtitles
import runtime_publication as publication
from runtime_meta import CacheIdentityError, invalidation_path, metadata_path, validate_cache, write_metadata


class IndexSubtitlesTests(unittest.TestCase):
    setUp = title_fixtures.IndexTitlesTests.setUp

    def prepare(self):
        self.output = self.paths.cache_dir / 'subtitle_index.json'
        self.srt = self.root / 'input.srt'
        self.srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nSynthetic cue\n')

    def generate(self):
        with patch.object(subtitles, 'media_duration', return_value=2.0):
            return subtitles.generate(self.paths)

    def test_success_and_cache_hit(self):
        self.prepare()
        self.generate()
        before = self.output.read_bytes(), metadata_path(self.output).read_bytes(), self.paths.state_path.read_bytes()
        with patch.object(subtitles, 'media_duration', side_effect=AssertionError('should reuse')):
            subtitles.generate(self.paths)
        self.assertEqual(before, (self.output.read_bytes(), metadata_path(self.output).read_bytes(), self.paths.state_path.read_bytes()))
        validate_cache(self.paths, self.output)

    def mutation_case(self, mutate):
        self.prepare()
        self.generate()
        self.srt.write_text(self.srt.read_text() + '\n2\n00:00:01,000 --> 00:00:02,000\nNew cue\n')
        pair = self.output.read_bytes(), metadata_path(self.output).read_bytes()
        state_after_mutation = []
        parse = subtitles.parse_srt
        def changed(*args):
            result = parse(*args)
            mutate()
            state_after_mutation.append(self.paths.state_path.read_bytes())
            return result
        with patch.object(subtitles, 'parse_srt', side_effect=changed), patch.object(subtitles, 'media_duration', return_value=2.0):
            with self.assertRaisesRegex(CacheIdentityError, 'inputs_changed'):
                subtitles.generate(self.paths)
        self.assertEqual(pair, (self.output.read_bytes(), metadata_path(self.output).read_bytes()))
        self.assertEqual(self.paths.state_path.read_bytes(), state_after_mutation[0])

    def test_srt_changes_during_build(self):
        self.mutation_case(lambda: self.srt.write_text('changed subtitle'))

    def test_video_identity_changes_during_build(self):
        self.mutation_case(lambda: (self.root / 'input.mp4').write_bytes(b'changed video identity'))

    def test_state_changes_during_build(self):
        self.mutation_case(lambda: self.paths.state_path.write_text(json.dumps({**self.state, 'new_field': 'changed'})))

    def test_rules_change_during_build(self):
        self.mutation_case(lambda: (self.repo / 'PROJECT_RULES.md').write_text('changed rules'))

    def test_config_changes_during_build(self):
        self.mutation_case(lambda: (self.repo / 'config/content_rules.json').write_text('{"new":true}'))

    def test_title_input_changes_during_build(self):
        self.mutation_case(lambda: self.source.write_bytes(b'changed workbook'))

    def test_failure_at_each_publication_step_blocks_reads_without_state_advance(self):
        self.prepare()
        self.generate()
        self.srt.write_text(self.srt.read_text() + '\n2\n00:00:01,000 --> 00:00:02,000\nNew cue\n')
        for target in (self.output, metadata_path(self.output), self.paths.state_path):
            with self.subTest(target=target.name):
                state_before = self.paths.state_path.read_bytes()
                replace = os.replace
                def fault(source, destination):
                    if Path(destination) == target:
                        raise OSError('publication fault')
                    return replace(source, destination)
                with patch.object(publication.os, 'replace', side_effect=fault), self.assertRaisesRegex(OSError, 'publication fault'):
                    self.generate()
                self.assertEqual(state_before, self.paths.state_path.read_bytes())
                with self.assertRaisesRegex(CacheIdentityError, 'publication_incomplete'):
                    validate_cache(self.paths, self.output)
        self.generate()
        validate_cache(self.paths, self.output)

    def test_legacy_current_identity_requires_regeneration(self):
        self.prepare()
        self.generate()
        old = json.loads(self.output.read_bytes())
        old.pop('generator')
        self.output.write_text(json.dumps(old))
        write_metadata(self.paths, self.output, 'index_subtitles.py')
        with self.assertRaisesRegex(CacheIdentityError, 'subtitle_generator_provenance_invalid'):
            validate_cache(self.paths, self.output)
        self.generate()
        validate_cache(self.paths, self.output)

    def test_marker_removal_failure_rolls_back_state_too(self):
        self.prepare()
        state_before = self.paths.state_path.read_bytes()
        unlink = Path.unlink
        def fault(path, *args, **kwargs):
            if path == invalidation_path(self.output):
                raise OSError('marker removal fault')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fault), self.assertRaisesRegex(OSError, 'marker removal fault'):
            self.generate()
        self.assertEqual(self.paths.state_path.read_bytes(), state_before)
        with self.assertRaisesRegex(CacheIdentityError, 'publication_incomplete'):
            validate_cache(self.paths, self.output)

    def test_input_change_between_renames_rolls_back_and_does_not_update_state(self):
        self.prepare()
        state_before = self.paths.state_path.read_bytes()
        replace = os.replace
        def changed(source, target):
            result = replace(source, target)
            if Path(target) == self.output:
                self.srt.write_text('changed during publish')
            return result
        with patch.object(publication.os, 'replace', side_effect=changed), self.assertRaisesRegex(CacheIdentityError, 'inputs_changed'):
            self.generate()
        self.assertEqual(state_before, self.paths.state_path.read_bytes())
        self.assertFalse(self.output.exists())
        self.assertFalse(metadata_path(self.output).exists())

    def test_concurrent_process_generator_fails_fast_and_first_completes(self):
        self.prepare()
        context = multiprocessing.get_context('fork')
        entered, release = context.Event(), context.Event()
        def worker():
            def duration(_):
                entered.set()
                if not release.wait(15):
                    raise RuntimeError('test synchronization timeout')
                return 2.0
            with patch.object(subtitles, 'media_duration', side_effect=duration):
                subtitles.generate(self.paths)
        process = context.Process(target=worker)
        process.start()
        try:
            self.assertTrue(entered.wait(10))
            with self.assertRaises(BlockingIOError):
                self.generate()
            self.assertFalse(self.output.exists())
        finally:
            release.set()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join()
        self.assertEqual(process.exitcode, 0)
        validate_cache(self.paths, self.output)
