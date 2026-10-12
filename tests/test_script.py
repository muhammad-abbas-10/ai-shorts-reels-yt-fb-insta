from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from shorts.config import Settings
from shorts.models import JobConfig
from shorts.script import _json_object, generate_script


class ScriptResponseTests(unittest.TestCase):
    def test_accepts_valid_json_inside_explanatory_text(self):
        content = ('Here is the script:\n```json\n'
                   '{"title":"A quiet sky","narration":"Take a breath and look at the stars tonight.",'
                   '"description":"A calm look upward","hashtags":[],"footage_queries":["night sky"]}'
                   '\n```\nEnjoy.')
        self.assertEqual(_json_object(content)["title"], "A quiet sky")

    def test_retries_cut_off_or_malformed_free_gemma_responses(self):
        settings = replace(Settings.from_env(), llm_api_key="unit-test",
                           llm_base_url="https://openrouter.ai/api/v1",
                           llm_model="google/gemma-4-26b-a4b-it:free",
                           llm_fallback_model="openrouter/free", retry_limit=1)
        config = JobConfig(topic="A quiet sky", duration_seconds=10,
                           source_notes="Stars are visible in a clear night sky.")
        valid = {"title": "A quiet sky", "narration": "Take a breath and look at the stars tonight.",
                 "description": "A calm look upward", "hashtags": [],
                 "footage_queries": ["night sky"]}
        bad_answers = [
            {"choices": [{"finish_reason": "length", "message": {"content": '{"title":"A'}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "Here is your script."}}]},
        ]
        for bad in bad_answers:
            with self.subTest(bad=bad["choices"][0]["finish_reason"]):
                good = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid)}}]}
                with patch("shorts.script.request_json", side_effect=[bad, good]) as request:
                    result = generate_script(config, settings)
                self.assertEqual(result.title, "A quiet sky")
                self.assertEqual(request.call_count, 2)
                first = request.call_args_list[0].kwargs["json"]
                second = request.call_args_list[1].kwargs["json"]
                self.assertEqual(first["response_format"], {"type": "json_object"})
                self.assertEqual(first["provider"], {"require_parameters": True})
                self.assertEqual(first["models"], ["openrouter/free"])
                self.assertGreater(second["max_completion_tokens"], first["max_completion_tokens"])
                self.assertEqual(second["temperature"], 0.1)


if __name__ == "__main__":
    unittest.main()
