import unittest

from antigravity_auth.search import (
    SearchResult,
    generate_request_id,
    format_search_result,
    parse_search_response,
)


class TestGenerateRequestId(unittest.TestCase):
    def test_returns_string(self):
        request_id = generate_request_id()
        self.assertIsInstance(request_id, str)
        self.assertTrue(len(request_id) > 0)

    def test_starts_with_search_prefix(self):
        request_id = generate_request_id()
        self.assertTrue(request_id.startswith("search-"))

    def test_unique_ids(self):
        ids = {generate_request_id() for _ in range(10)}
        self.assertEqual(len(ids), 10)


class TestFormatSearchResult(unittest.TestCase):
    def test_empty_result(self):
        result = SearchResult()
        output = format_search_result(result)
        self.assertIn("## Search Results", output)
        self.assertEqual(result.text, "")
        self.assertEqual(result.sources, [])
        self.assertEqual(result.searchQueries, [])

    def test_with_text_only(self):
        result = SearchResult(text="Hello world")
        output = format_search_result(result)
        self.assertIn("Hello world", output)
        self.assertNotIn("### Sources", output)
        self.assertNotIn("### URLs Retrieved", output)
        self.assertNotIn("### Search Queries Used", output)

    def test_with_sources(self):
        result = SearchResult(
            text="Some result text",
            sources=[{"title": "Example", "url": "https://example.com"}],
        )
        output = format_search_result(result)
        self.assertIn("### Sources", output)
        self.assertIn("[Example](https://example.com)", output)

    def test_with_urls_retrieved(self):
        result = SearchResult(
            text="Result text",
            urlsRetrieved=[
                {"url": "https://example.com/page", "status": "URL_RETRIEVAL_STATUS_SUCCESS"},
                {"url": "https://example.org", "status": "URL_RETRIEVAL_STATUS_FAILED"},
            ],
        )
        output = format_search_result(result)
        self.assertIn("### URLs Retrieved", output)
        self.assertIn("\u2713 https://example.com/page", output)
        self.assertIn("\u2717 https://example.org", output)

    def test_with_search_queries(self):
        result = SearchResult(
            text="Result text",
            searchQueries=["python programming", "python vs javascript"],
        )
        output = format_search_result(result)
        self.assertIn("### Search Queries Used", output)
        self.assertIn('"python programming"', output)
        self.assertIn('"python vs javascript"', output)

    def test_full_result(self):
        result = SearchResult(
            text="Full result with all fields.",
            sources=[{"title": "Src1", "url": "https://src1.com"}],
            searchQueries=["test query"],
            urlsRetrieved=[{"url": "https://page.com", "status": "URL_RETRIEVAL_STATUS_SUCCESS"}],
        )
        output = format_search_result(result)
        self.assertIn("## Search Results", output)
        self.assertIn("Full result with all fields", output)
        self.assertIn("### Sources", output)
        self.assertIn("### URLs Retrieved", output)
        self.assertIn("### Search Queries Used", output)


class TestParseSearchResponse(unittest.TestCase):
    def test_empty_data(self):
        result = parse_search_response({})
        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.text, "")
        self.assertEqual(result.sources, [])

    def test_no_response_no_candidates(self):
        result = parse_search_response({"response": {}})
        self.assertEqual(result.text, "")

        result2 = parse_search_response({"response": {"candidates": []}})
        self.assertEqual(result2.text, "")

    def test_error_in_data(self):
        data = {
            "error": {"message": "Rate limit exceeded"},
            "response": {},
        }
        result = parse_search_response(data)
        self.assertIn("Error: Rate limit exceeded", result.text)

    def test_error_in_response(self):
        data = {
            "response": {
                "error": {"message": "Model not available"},
            },
        }
        result = parse_search_response(data)
        self.assertIn("Error: Model not available", result.text)

    def test_no_message_in_error(self):
        data = {
            "error": {"code": 403},
            "response": {},
        }
        result = parse_search_response(data)
        self.assertIn("Error: Unknown error", result.text)

    def test_response_with_candidate_text_only(self):
        data = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Hello world"}],
                        },
                    },
                ],
            },
        }
        result = parse_search_response(data)
        self.assertEqual(result.text, "Hello world")

    def test_response_with_multiple_parts(self):
        data = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Part one"},
                                {"text": "Part two"},
                            ],
                        },
                    },
                ],
            },
        }
        result = parse_search_response(data)
        self.assertEqual(result.text, "Part one\nPart two")

    def test_full_grounding_data(self):
        data = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Grounded result"}],
                        },
                        "groundingMetadata": {
                            "webSearchQueries": ["test query"],
                            "groundingChunks": [
                                {"web": {"uri": "https://example.com", "title": "Example"}},
                                {"web": {"uri": "https://test.org", "title": "Test"}},
                            ],
                        },
                    },
                ],
            },
        }
        result = parse_search_response(data)
        self.assertEqual(result.text, "Grounded result")
        self.assertEqual(result.searchQueries, ["test query"])
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(result.sources[0]["title"], "Example")
        self.assertEqual(result.sources[1]["url"], "https://test.org")

    def test_url_context_metadata(self):
        data = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "URL result"}],
                        },
                        "urlContextMetadata": {
                            "url_metadata": [
                                {"retrieved_url": "https://page.com", "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS"},
                                {"retrieved_url": "https://broken.com", "url_retrieval_status": "URL_RETRIEVAL_STATUS_FAILED"},
                            ],
                        },
                    },
                ],
            },
        }
        result = parse_search_response(data)
        self.assertEqual(len(result.urlsRetrieved), 2)
        self.assertEqual(result.urlsRetrieved[0]["url"], "https://page.com")
        self.assertEqual(result.urlsRetrieved[1]["status"], "URL_RETRIEVAL_STATUS_FAILED")

    def test_grounding_chunk_without_title_or_uri(self):
        data = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Partial grounding"}],
                        },
                        "groundingMetadata": {
                            "groundingChunks": [
                                {"web": {}},
                                {"web": {"uri": "https://valid.com", "title": "Valid"}},
                            ],
                        },
                    },
                ],
            },
        }
        result = parse_search_response(data)
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0]["url"], "https://valid.com")


if __name__ == "__main__":
    unittest.main()
